"""Actual paced runner + binding + scorer, simulated live-case boundary.

Import-time live_evaluation dependency is explicitly mocked: this tests pacing
and metadata integration, not agent, SDK, helper implementation or policy gate.
"""
import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import src
from src.bound_experiment import save_bound_rehearsal
from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import load_fixtures
from src.evaluation_metrics import score_report
from src.experiment_metadata import REQUIRED, build_experiment_metadata
from src.paced_experiment_binding import rehearse_paced_experiment


class FakeClock:
    def __init__(self): self.now=0.0; self.pauses=[]
    def clock(self): return self.now
    def sleep(self, seconds): self.pauses.append(seconds); self.now+=seconds


class MockAdapter:
    model='test-model'
    def chat(self, *a, **kw): raise AssertionError('No SDK traffic allowed')
    def continue_with_tool_results(self, *a, **kw): raise AssertionError('No SDK traffic allowed')


class Simulated429(Exception):
    status_code=429


class PacedBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name in REQUIRED:
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# source\n')
        (self.root/'src/config.py').write_text("PROVIDERS={'anthropic':{'adapter':'anthropic','model':'test-model'},'gemini':{'adapter':'openai_compatible','model':'test-model'}}")
        for name,data in [('benchmark_cases_synthetic_v2.json',load_benchmark()),('scenario_fixtures_synthetic_v2.json',load_fixtures())]:
            (self.root/'evaluation'/name).write_text(json.dumps(data))
        self.clock=FakeClock();self.seen=[]
        self.plan=build_experiment_metadata(self.root,provider='anthropic',between_case_seconds=7,retry_delay_seconds=11)
        # Load the real runner source under a private name so test doubles cannot
        # leak into the normal application module through the import cache.
        fake_live=types.ModuleType('src.live_evaluation')
        def original(*a,**kw): raise AssertionError('Unpatched case execution')
        fake_live.run_live_case=original
        path=Path(src.__path__[0])/'paced_assisted_benchmark.py'
        spec=importlib.util.spec_from_file_location('_paced_binding_test_runner',path)
        self.paced=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'src.live_evaluation':fake_live}): spec.loader.exec_module(self.paced)
        self.original=self.paced.run_live_case
        self.override=patch.object(src,'paced_assisted_benchmark',self.paced,create=True)
        self.override.start();self.addCleanup(self.override.stop)

    def execute(self,case,fixture,adapter,**kwargs):
        self.seen.append((case['id'],copy.deepcopy(kwargs)))
        self.clock.now+=1
        e=case['expected']
        return dict(answer=' '.join(e['public_phrases']) if e['publishable'] else '',completed=True,
                    status='passed' if e['publishable'] else 'incomplete',publishable=e['publishable'],
                    next_action=e['next_action'],reason_codes=e['reason_codes'],tool_rounds=1,
                    tool_evidence=[{'tool_name':'evaluate_policy_signals','result':{}},
                     {'tool_name':'retrieve_policy','result':[{'source':p+'_mock.md'} for p in e['required_policy_ids']]}])

    def run_bound(self,executor=None):
        return rehearse_paced_experiment(self.plan,self.root,MockAdapter(),case_executor=executor or self.execute,
                                          sleeper=self.clock.sleep,clock=self.clock.clock)

    def test_real_six_case_runner_and_metadata(self):
        report,meta=self.run_bound()
        self.assertEqual(score_report(report)['metrics']['cases_passed_all_checks'],6)
        self.assertEqual(meta['execution_record']['case_attempt_counts'],{f'CF-{i:03}':1 for i in range(1,7)})
        self.assertEqual(meta['record_kind'],'offline_runner_rehearsal')
        self.assertFalse(meta['live_provider_execution_verified'])

    def test_settings_reach_every_case(self):
        self.run_bound()
        self.assertEqual(len(self.seen),6)
        self.assertTrue(all(k==dict(max_tool_rounds=4,policy_passage_variant='clarified_v1',policy_tool_suite=True) for _,k in self.seen))

    def test_between_case_pauses_excluded_from_case_time(self):
        report,_=self.run_bound()
        self.assertEqual(self.clock.pauses,[7.0]*5)
        self.assertTrue(all(r['elapsed_ms']==1000.0 for r in report['cases']))

    def test_one_429_retried_and_recorded(self):
        failed=False
        def execute(*a,**kw):
            nonlocal failed
            if not failed: failed=True;raise Simulated429('PRIVATE')
            return self.execute(*a,**kw)
        report,meta=self.run_bound(execute)
        self.assertEqual(meta['execution_record']['actual_retries']['CF-001'],1)
        self.assertEqual(report['cases'][0]['elapsed_ms'],12000.0)
        self.assertNotIn('PRIVATE',json.dumps(meta)+json.dumps(report))

    def test_exhausted_429_is_error_not_block(self):
        def execute(case,*a,**kw):
            if case['id']=='CF-004': raise Simulated429('PRIVATE')
            return self.execute(case,*a,**kw)
        report,meta=self.run_bound(execute)
        self.assertIsNone(report['cases'][3]['observed'])
        self.assertFalse(score_report(report)['cases'][3]['passed'])
        self.assertEqual(meta['execution_record']['case_attempt_counts']['CF-004'],2)

    def test_non429_not_retried(self):
        def execute(case,*a,**kw):
            if case['id']=='CF-001': raise ValueError('PRIVATE')
            return self.execute(case,*a,**kw)
        report,meta=self.run_bound(execute)
        self.assertEqual(report['cases'][0]['error_type'],'ValueError')
        self.assertEqual(meta['execution_record']['actual_retries']['CF-001'],0)
        self.assertEqual(meta['execution_record']['case_exception_types']['CF-001'],['ValueError'])
        self.assertNotIn('PRIVATE',json.dumps(meta))

    def test_dispatch_reference_restored(self):
        self.run_bound()
        self.assertIs(self.paced.run_live_case,self.original)

    def test_stale_source_no_case_invocation(self):
        (self.root/'src/agent.py').write_text('# changed')
        with self.assertRaises(ValueError):self.run_bound()
        self.assertEqual(self.seen,[])
        self.assertEqual(self.clock.pauses,[])

    def test_zero_attempts_when_cooldown_fails(self):
        def broken_sleep(_):raise OSError('PRIVATE')
        report,meta=rehearse_paced_experiment(self.plan,self.root,MockAdapter(),case_executor=self.execute,
                                             sleeper=broken_sleep,clock=self.clock.clock)
        self.assertEqual(meta['execution_record']['case_attempt_counts']['CF-002'],0)
        self.assertEqual(report['cases'][1]['error_type'],'OSError')

    def test_saved_bundle_keeps_binding(self):
        import hashlib
        report,meta=self.run_bound();p=self.root/'bundle'
        save_bound_rehearsal(report,meta,p)
        self.assertEqual(hashlib.sha256((p/'observations.json').read_bytes()).hexdigest(),meta['execution_record']['report_sha256'])
        with self.assertRaises(FileExistsError):save_bound_rehearsal(report,meta,p)

    def test_both_provider_plans_use_same_case_options(self):
        self.run_bound();first=copy.deepcopy(self.seen);self.seen=[]
        self.plan=build_experiment_metadata(self.root,provider='gemini',between_case_seconds=7,retry_delay_seconds=11)
        report,_=self.run_bound()
        self.assertEqual(self.seen,first)
        self.assertTrue(report['provider_id'].startswith('gemini.'))

    def test_input_plan_unchanged(self):
        before=copy.deepcopy(self.plan);self.run_bound();self.assertEqual(self.plan,before)
