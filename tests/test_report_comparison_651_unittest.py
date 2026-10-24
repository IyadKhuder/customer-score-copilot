"""Dependency-free integrity checks for the comparator."""
import copy
import json
import unittest
from pathlib import Path
from src.report_comparison import compare_reports, render_markdown
from src.evaluation_metrics import score_report


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        def read(p): return json.loads((root / p).read_text(encoding='utf-8'))
        self.b = read('evaluation/benchmark_cases_synthetic_v2.json')
        self.f = read('evaluation/scenario_fixtures_synthetic_v2.json')
        # Build valid observations directly from the frozen contract; no API calls.
        from src.evaluation_runner import run_benchmark
        def execute(case, fixture):
            e = case['expected']
            return dict(answer=' '.join(e['public_phrases']) if e['publishable'] else '',
                        status='passed' if e['publishable'] else 'incomplete',
                        publishable=e['publishable'], next_action=e['next_action'],
                        reason_codes=e['reason_codes'], completed=True, tool_rounds=1,
                        tool_evidence=[dict(tool_name='retrieve_policy', result=[
                            dict(source=p+'_memo.md', text='synthetic') for p in fixture['policy_ids']])])
        r = run_benchmark(execute, provider_id='test', benchmark=self.b, fixtures=self.f)
        self.e = dict(label='test', run=r, scores=score_report(r, benchmark=self.b, fixtures=self.f))

    def compare(self, entries=None):
        return compare_reports([self.e] if entries is None else entries, benchmark=self.b, fixtures=self.f)

    def test_valid_and_immutable(self):
        old = copy.deepcopy(self.e)
        result = self.compare()
        self.assertEqual(old, self.e)
        self.assertTrue(result['runs'][0]['cases'][3]['passed'])
        self.assertFalse(result['causal_claims_supported'])
        self.assertIn('No causal improvement', render_markdown(result))

    def test_wrong_hashes(self):
        for field in ('benchmark_sha256', 'fixture_sha256'):
            with self.subTest(field=field):
                entry = copy.deepcopy(self.e)
                entry['run'][field] = '0'*64
                with self.assertRaises(ValueError): self.compare([entry])

    def test_missing_duplicate_reordered(self):
        for mode in ('missing', 'duplicate', 'reordered'):
            with self.subTest(mode=mode):
                entry = copy.deepcopy(self.e)
                rows = entry['run']['cases']
                if mode == 'missing': rows.pop()
                elif mode == 'duplicate': rows[-1] = rows[0]
                else: rows.reverse()
                with self.assertRaises(ValueError): self.compare([entry])

    def test_stale_scores(self):
        self.e['scores']['metrics']['cases_total'] = 99
        with self.assertRaises(ValueError): self.compare()

    def test_error_is_not_block(self):
        r = self.e['run']['cases'][3]
        r.update(observed=None, error_type='RateLimitError')
        self.e['scores'] = score_report(self.e['run'], benchmark=self.b, fixtures=self.f)
        result = self.compare()['runs'][0]['cases'][3]
        self.assertEqual('ERROR', result['decision'])
        self.assertFalse(result['passed'])

    def test_wrong_reason_is_not_full_pass(self):
        self.e['run']['cases'][3]['observed']['reason_codes'] = ['COMPOSITION_UNSUPPORTED']
        self.e['scores'] = score_report(self.e['run'], benchmark=self.b, fixtures=self.f)
        result = self.compare()['runs'][0]['cases'][3]
        self.assertTrue(result['publication_match'])
        self.assertFalse(result['passed'])

    def test_mixed_variants(self):
        other = copy.deepcopy(self.e)
        other.update(label='other', condition={'policy_passages': 'clarified_v1'})
        self.e['condition'] = {'policy_passages': 'baseline'}
        flags = self.compare([self.e, other])['comparability_flags']
        self.assertIn('policy_passages: mixed conditions', flags)
        self.assertIn('code_revision: incomplete provenance', flags)

    def test_invalid_conditions_and_labels(self):
        with self.assertRaises(ValueError): self.compare([])
        with self.assertRaises(ValueError): self.compare([self.e, self.e])
        self.e['condition'] = {'causal_improvement': 'yes'}
        with self.assertRaises(ValueError): self.compare()
