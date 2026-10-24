"""Module 6.4.3: versioned synthetic policy evidence, offline only."""
from __future__ import annotations

import copy
import sys
import types
from unittest.mock import patch

import pytest

import src
from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import _SCRIPTED_POLICY_DOCS, load_fixtures
from src.live_evaluation import run_live_case
from src.policy_passage_variants import POLICY_PASSAGE_VARIANTS, policy_passages
from scripts.diagnose_policy_evidence_643 import main, summarize


class FakeClient:
    def chat(self, *, messages, tools):
        return None

    def continue_with_tool_results(self, *, assistant_response, tool_results, tools):
        return None


def fixture_pair(index=0):
    bench = load_benchmark()
    case = bench['cases'][index]
    fixture = next(item for item in load_fixtures(benchmark=bench)['fixtures']
                   if item['scenario_id'] == case['scenario_id'])
    return case, fixture


def _mock_agent():
    state = {'seen': []}
    module = types.ModuleType('src.agent')
    module.ALL_TOOLS = [{'name': 'get_history'}, {'name': 'retrieve_policy'},
                        {'name': 'get_latest_score'}]
    module.ALL_TOOL_DISPATCH = {'get_latest_score': lambda **kwargs: None}

    def execute(question, client, **kwargs):
        assert set(module.ALL_TOOL_DISPATCH) == {'get_history', 'retrieve_policy'}
        docs = module.ALL_TOOL_DISPATCH['retrieve_policy'](query='POL-002', k=3)
        state['seen'].append((question, kwargs, docs))
        return {'answer': 'blocked', 'publishable': False, 'next_action': 'revise_response',
                'reason_codes': ['POLICY_MISMATCH'], 'tool_rounds': 1,
                'verification': {'policy': {'status': 'failed', 'checks': []}}}

    module.run_agent = execute
    return module, state


@pytest.mark.parametrize('policy_ids', [['POL-002'], ['POL-002', 'POL-004'],
                                        ['POL-002', 'POL-005'],
                                        ['POL-002', 'POL-004', 'POL-005']])
def test_baseline_docs_exactly_unchanged(policy_ids):
    assert policy_passages(policy_ids) == [copy.deepcopy(_SCRIPTED_POLICY_DOCS[x]) for x in policy_ids]


def test_clarified_general_event_units_and_dates():
    docs = policy_passages(['POL-002'], variant='clarified_v1')
    passage = docs[0]['text']
    assert 'FOUR distinct' in passage and 'THREE downward' in passage
    assert 'FINAL observation' in passage
    assert 'Older baseline observations can' in passage
    assert 'not the individual score decreases' in passage
    assert '2025' not in passage and '81000001' not in passage
    assert docs[0]['source'] == _SCRIPTED_POLICY_DOCS['POL-002']['source']


def test_clarified_grace_and_quality_are_general():
    docs = policy_passages(['POL-004', 'POL-005'], variant='clarified_v1')
    assert 'FOUR consecutive transitions' in docs[0]['text']
    assert 'quarterly' not in docs[0]['text'].lower() or 'review' in docs[0]['text'].lower()
    assert 'EXACT' in docs[1]['text']
    assert all('83000005' not in d['text'] and '2025-04-01' not in d['text'] for d in docs)


def test_baseline_is_not_mutated():
    before = copy.deepcopy(_SCRIPTED_POLICY_DOCS)
    variant_docs = policy_passages(['POL-002'], variant='clarified_v1')
    variant_docs[0]['text'] = 'overwritten'
    assert _SCRIPTED_POLICY_DOCS == before
    baseline_docs = policy_passages(['POL-002'])
    baseline_docs[0]['source'] = 'changed'
    assert _SCRIPTED_POLICY_DOCS == before


@pytest.mark.parametrize('bad_variant', ['other', '', None])
def test_unsupported_variant_blocked(bad_variant):
    with pytest.raises(ValueError):
        policy_passages(['POL-002'], variant=bad_variant)


@pytest.mark.parametrize('bad', [['POL-999'], ['POL-002', None], None, 'POL-002'])
def test_unknown_policy_ids_blocked(bad):
    with pytest.raises(ValueError):
        policy_passages(bad)


def test_live_case_default_does_not_change_existing_path():
    case, fixture = fixture_pair()
    module, state = _mock_agent()
    with patch.dict(sys.modules, {'src.agent': module}), patch.object(src, 'agent', module, create=True):
        run_live_case(case, fixture, FakeClient())
    assert state['seen'][0][2] == policy_passages(fixture['policy_ids'])
    assert state['seen'][0][0] == case['question']


def test_live_case_clarified_does_not_change_case_or_fixture():
    case, fixture = fixture_pair()
    before = copy.deepcopy((case, fixture))
    module, state = _mock_agent()
    with patch.dict(sys.modules, {'src.agent': module}), patch.object(src, 'agent', module, create=True):
        run_live_case(case, fixture, FakeClient(), policy_passage_variant='clarified_v1')
    assert state['seen'][0][2] == policy_passages(fixture['policy_ids'], variant='clarified_v1')
    assert (case, fixture) == before


def test_live_case_invalid_variant_aborts_before_provider_or_agent():
    with pytest.raises(ValueError, match='variant'):
        run_live_case(*fixture_pair(), FakeClient(), policy_passage_variant='invalid')


@pytest.mark.parametrize('case_idx', range(6))
def test_all_scenarios_retain_original_policy_identifiers(case_idx):
    case, fixture = fixture_pair(case_idx)
    module, state = _mock_agent()
    with patch.dict(sys.modules, {'src.agent': module}), patch.object(src, 'agent', module, create=True):
        run_live_case(case, fixture, FakeClient(), policy_passage_variant='clarified_v1')
    docs = state['seen'][0][2]
    assert [d['source'] for d in docs] == [
        _SCRIPTED_POLICY_DOCS[x]['source'] for x in fixture['policy_ids']]


def test_diagnostic_requires_explicit_live_opt_in():
    with pytest.raises(SystemExit) as exc:
        main(['--case', 'CF-001', '--provider', 'gemini', '--policy-passages', 'clarified_v1'])
    assert exc.value.code != 0


def test_diagnostic_runs_only_selected_case_with_no_expected_answers(capsys):
    seen = []

    def runner(case, fixture, client, *, policy_passage_variant):
        seen.append((case, fixture, client, policy_passage_variant))
        return {'publishable': False, 'next_action': 'revise_response',
                'reason_codes': ['POLICY_MISMATCH'], 'tool_rounds': 1,
                'verification': {'schema': {'status': 'passed'},
                                 'policy': {'status': 'failed', 'checks': []}}}

    code = main(['--case', 'CF-001', '--provider', 'gemini',
                 '--policy-passages', 'clarified_v1', '--confirm-live-api'],
                client_factory=lambda provider: FakeClient(), case_runner=runner)
    out = capsys.readouterr().out
    assert code == 0 and len(seen) == 1
    assert 'CF-001' in out and 'clarified_v1' in out
    assert 'PUBLISH' not in out and 'POLICY_MISMATCH' in out
    assert 'expected' not in seen[0][0].get('request', {})


def test_summary_never_displays_untrusted_llm_draft():
    result = {'answer': 'SECRET UNPUBLISHED MODEL ANSWER', 'publishable': False,
              'next_action': 'revise_response', 'reason_codes': ['POLICY_MISMATCH'],
              'verification': {'answer': 'SECRET DRAFT', 'policy': {
                  'checks': [{'claim': {'field': 'raw_event_count.consecutive_declines'},
                              'status': 'failed', 'audit': {'checks': [{
                                  'field': 'raw_event_count.consecutive_declines',
                                  'claimed': 0, 'expected': 1}]}}]}}}
    text = '\n'.join(summarize(result))
    assert 'SECRET' not in text
    assert 'Claimed: 0; expected: 1' in text


def test_variants_are_two_explicit_supported_modes():
    assert POLICY_PASSAGE_VARIANTS == ('baseline', 'clarified_v1')
