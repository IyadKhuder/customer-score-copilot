"""Offline rehearsal contracts; real frozen dataset, no SDK or real agent."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.bound_experiment import rehearse_bound_experiment, save_bound_rehearsal
from src.experiment_metadata import REQUIRED, build_experiment_metadata
from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import load_fixtures, run_benchmark


class BoundExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in REQUIRED:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('# synthetic source\n', encoding='utf-8')
        (self.root/'src/config.py').write_text("PROVIDERS = {'anthropic': {'adapter':'anthropic', 'model':'mock-model'}}")
        for name, data in [('benchmark_cases_synthetic_v2.json',load_benchmark()),
                           ('scenario_fixtures_synthetic_v2.json',load_fixtures())]:
            (self.root/'evaluation'/name).write_text(json.dumps(data))
        self.plan = build_experiment_metadata(self.root, provider='anthropic')
        self.client = SimpleNamespace(model='mock-model')
        self.calls = []

    def runner(self, client, **kw):
        self.calls.append(kw)
        def execute(case, fixture):
            e = case['expected']
            return dict(answer=' '.join(e['public_phrases']) if e['publishable'] else '',
                        status='passed' if e['publishable'] else 'incomplete',
                        publishable=e['publishable'], next_action=e['next_action'],
                        reason_codes=e['reason_codes'], completed=True, tool_rounds=1,
                        tool_evidence=[])
        return run_benchmark(execute, provider_id=kw['provider_id'],
                             execution_mode='provider_adapter', benchmark=kw['benchmark'], fixtures=kw['fixtures'])

    def run_bound(self, runner=None):
        return rehearse_bound_experiment(self.plan, self.root, self.client,
                                        runner=self.runner if runner is None else runner)

    def test_settings_forwarded_from_plan(self):
        r,m = self.run_bound()
        self.assertEqual(self.calls[0]['policy_passage_variant'], 'clarified_v1')
        self.assertEqual(self.calls[0]['between_case_seconds'],65.0)
        self.assertEqual(self.calls[0]['max_rate_limit_retries'],1)
        self.assertFalse(m['execution_verified'])
        self.assertEqual(m['record_kind'],'offline_runner_rehearsal')
        self.assertTrue(m['execution_record']['source_snapshot_unchanged_after_run'])
        self.assertFalse(r['cases'][3]['observed']['publishable'])

    def test_plan_not_mutated(self):
        old=copy.deepcopy(self.plan)
        self.run_bound()
        self.assertEqual(old,self.plan)

    def test_source_drift_rejected_before_runner(self):
        (self.root/'src/agent.py').write_text('# changed')
        with self.assertRaises(ValueError): self.run_bound()
        self.assertEqual(self.calls,[])

    def test_client_model_mismatch(self):
        self.client.model='other'
        with self.assertRaises(ValueError): self.run_bound()
        self.assertEqual(self.calls,[])

    def test_unsupported_plan_setting(self):
        self.plan['planned_settings']['policy_tool_suite']=False
        with self.assertRaises(ValueError): self.run_bound()
        self.assertEqual(self.calls,[])

    def test_execution_plan_cannot_be_reused(self):
        self.plan['execution_record']={}
        with self.assertRaises(ValueError): self.run_bound()

    def test_wrong_report_fingerprint(self):
        def wrong(c,**kw):
            r=self.runner(c,**kw); r['fixture_sha256']='0'*64; return r
        with self.assertRaises(ValueError): self.run_bound(wrong)

    def test_wrong_provider_identity(self):
        def wrong(c,**kw):
            r=self.runner(c,**kw); r['provider_id']='another'; return r
        with self.assertRaises(ValueError): self.run_bound(wrong)

    def test_post_run_drift_recorded(self):
        def changed(c,**kw):
            r=self.runner(c,**kw)
            (self.root/'src/agent.py').write_text('# changed during run')
            return r
        r,m=self.run_bound(changed)
        self.assertFalse(m['execution_record']['source_snapshot_unchanged_after_run'])

    def test_saved_byte_hash_and_no_overwrite(self):
        r,m=self.run_bound(); p=self.root/'bundle'
        save_bound_rehearsal(r,m,p)
        self.assertEqual(hashlib.sha256((p/'observations.json').read_bytes()).hexdigest(),m['execution_record']['report_sha256'])
        with self.assertRaises(FileExistsError): save_bound_rehearsal(r,m,p)

    def test_tampered_report_not_saved(self):
        r,m=self.run_bound();r['cases'][0]['elapsed_ms']+=1
        with self.assertRaises(ValueError): save_bound_rehearsal(r,m,self.root/'bundle')
        self.assertFalse((self.root/'bundle').exists())

    def test_runner_failure_not_certified(self):
        def failure(*a,**kw): raise RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError): self.run_bound(failure)
