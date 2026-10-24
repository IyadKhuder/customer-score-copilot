"""Offline positive/negative tests for the optional trusted-policy-tool experiment."""
from __future__ import annotations

import copy
import sys
import types
from unittest.mock import patch

import pytest

import src
from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import load_fixtures
from src.live_evaluation import run_live_case
from src.policy_assist_experiment import (
    ASSIST_PROMPT_APPENDIX, ASSIST_TOOL_SCHEMA, build_assist_dispatch,
)
from scripts.diagnose_policy_assist_644 import main


class FakeClient:
    def chat(self, *, messages, tools):
        return None

    def continue_with_tool_results(self, *, assistant_response, tool_results, tools):
        return None


def pair(i=0):
    benchmark = load_benchmark()
    case = benchmark['cases'][i]
    fixture = next(f for f in load_fixtures(benchmark=benchmark)['fixtures']
                   if f['scenario_id'] == case['scenario_id'])
    return copy.deepcopy(case), copy.deepcopy(fixture)


def tool_for(i=0):
    case, f = pair(i)
    req = case['request']
    history = [dict(period=p, score=s, bin=bin_number, market=req['market'])
               for p, s, bin_number in zip(f['periods'], f['scores'], f['bins'])]
    return build_assist_dispatch(history, account_id=req['account_id'],
                                 as_of_period=req['as_of_period'])


def fake_agent(*, fail=False):
    mod = types.ModuleType('src.agent')
    mod.ALL_TOOLS = [{'name': 'get_history'}, {'name': 'retrieve_policy'},
                     {'name': 'get_latest_score'}]
    old = lambda **kwargs: []
    mod.ALL_TOOL_DISPATCH = {'get_latest_score': old}
    mod.CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS = 'ORIGINAL FULL REPORT PROMPT'
    snapshot = []

    def run_agent(question, llm, **kwargs):
        names = {tool['name'] for tool in mod.ALL_TOOLS}
        assert names == {'get_history', 'retrieve_policy', 'evaluate_policy_signals'}
        assert set(mod.ALL_TOOL_DISPATCH) == names
        assert 'CONTROLLED POLICY-TOOL EXPERIMENT' in mod.CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS
        assert kwargs['expected_account_id'] == 81000001
        assert kwargs['claim_scope'] == 'score_bin_warning_signal_decline_events'
        rows = mod.ALL_TOOL_DISPATCH['get_history'](account_id=81000001)
        docs = mod.ALL_TOOL_DISPATCH['retrieve_policy'](query='POL-002')
        evidence = mod.ALL_TOOL_DISPATCH['evaluate_policy_signals'](
            account_id=81000001, as_of_period='1991-12-01')
        snapshot.append((rows, docs, evidence))
        if fail:
            raise RuntimeError('simulated provider failure')
        return {'answer': 'safe draft excluded', 'publishable': True, 'reason_codes': [],
                'next_action': 'publish', 'tool_evidence': [], 'verification': {}}

    mod.run_agent = run_agent
    return mod, snapshot, old


def test_schema_is_minimal_and_has_no_fixed_case_result():
    assert ASSIST_TOOL_SCHEMA['name'] == 'evaluate_policy_signals'
    assert ASSIST_TOOL_SCHEMA['input_schema']['required'] == ['account_id', 'as_of_period']
    assert 'triggered' not in ASSIST_TOOL_SCHEMA['description']
    assert '2025' not in ASSIST_TOOL_SCHEMA['description']
    assert '81000001' not in ASSIST_TOOL_SCHEMA['description']


def test_prompt_references_engine_not_benchmark_answer():
    assert 'get_history' in ASSIST_PROMPT_APPENDIX
    assert 'retrieve_policy' in ASSIST_PROMPT_APPENDIX
    assert 'evaluate_policy_signals' in ASSIST_PROMPT_APPENDIX
    assert '1991-11-01' not in ASSIST_PROMPT_APPENDIX
    assert 'CF-001' not in ASSIST_PROMPT_APPENDIX


@pytest.mark.parametrize('i,expected_status,expected_count', [
    (0, 'triggered', 1),
    (1, 'not_triggered', 0),
    (2, 'pending_data_quality', 2),
    (3, 'insufficient_evidence', 0),
])
def test_standard_case_policy_computed_not_hardcoded(i, expected_status, expected_count):
    case, _ = pair(i)
    result = tool_for(i)(account_id=case['request']['account_id'],
                         as_of_period=case['request']['as_of_period'])
    assert result['warning_signal_status'] == expected_status
    assert result['consecutive_decline_event_count'] == expected_count


def test_cf001_event_end_is_inside_window_and_recovery_does_not_clear_it():
    result = tool_for(0)(account_id=81000001, as_of_period='1991-12-01')
    assert result['window_start'] == '1991-10-01'
    assert result['window_end'] == '1991-12-01'
    assert result['consecutive_decline_events'] == [
        {'period': '1991-11-01', 'review_state': 'eligible'},
    ]
    assert result['coverage_complete'] is True


@pytest.mark.parametrize('wrong', [0, 999, 83000005, None, True, '81000001', 81000001.0])
def test_cross_account_requests_fail_closed(wrong):
    with pytest.raises(ValueError, match='context mismatch'):
        tool_for(0)(account_id=wrong, as_of_period='1991-12-01')


@pytest.mark.parametrize('wrong_period', ['1991-11-01', '1992-01-01', '', None, True])
def test_wrong_dates_fail_closed(wrong_period):
    with pytest.raises(ValueError, match='context mismatch'):
        tool_for(0)(account_id=81000001, as_of_period=wrong_period)


def test_tool_uses_private_history_copy_not_caller_mutation():
    case, fixture = pair(0)
    rows = [dict(period=p, score=s, bin=bin_number, market=1)
            for p, s, bin_number in zip(fixture['periods'], fixture['scores'], fixture['bins'])]
    eval_fn = build_assist_dispatch(rows, account_id=81000001, as_of_period='1991-12-01')
    first = eval_fn(account_id=81000001, as_of_period='1991-12-01')
    rows[2]['score'] = 1_000_000
    second = eval_fn(account_id=81000001, as_of_period='1991-12-01')
    assert first == second


@pytest.mark.parametrize('bad_account,bad_period,bad_history', [
    (True, '1991-12-01', [{'period':'1991-12-01'}]),
    (81000001, False, [{'period':'1991-12-01'}]),
    (81000001, '1991-12-01', []),
    (81000001, '1991-12-01', ['not a row']),
])
def test_invalid_bound_context_fails_before_model(bad_account,bad_period,bad_history):
    with pytest.raises(ValueError):
        build_assist_dispatch(bad_history, account_id=bad_account, as_of_period=bad_period)


def test_assisted_mode_uses_only_three_tools_and_restores_original_state():
    case, f = pair()
    module, records, old = fake_agent()
    with patch.dict(sys.modules, {'src.agent': module}), patch.object(src, 'agent', module, create=True):
        result = run_live_case(case, f, FakeClient(), policy_passage_variant='clarified_v1',
                               policy_tool_assist=True)
    assert result['publishable'] is True
    assert records[0][2]['consecutive_decline_event_count'] == 1
    assert records[0][2]['warning_signal_status'] == 'triggered'
    assert len(records[0][1]) == 1
    assert module.ALL_TOOL_DISPATCH == {'get_latest_score': old}
    assert len(module.ALL_TOOLS) == 3
    assert module.CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS == 'ORIGINAL FULL REPORT PROMPT'


def test_assisted_mode_restores_while_provider_raises():
    case, f = pair()
    module, records, old = fake_agent(fail=True)
    with patch.dict(sys.modules, {'src.agent': module}), patch.object(src, 'agent', module, create=True):
        with pytest.raises(RuntimeError, match='simulated'):
            run_live_case(case, f, FakeClient(), policy_tool_assist=True)
    assert len(records) == 1
    assert module.ALL_TOOL_DISPATCH == {'get_latest_score': old}
    assert module.CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS == 'ORIGINAL FULL REPORT PROMPT'


@pytest.mark.parametrize('case_idx', [1, 2, 3, 4, 5])
def test_assisted_pilot_disallows_other_claim_scopes(case_idx):
    with pytest.raises(ValueError, match='supports only standard-market full reports'):
        run_live_case(*pair(case_idx), FakeClient(), policy_tool_assist=True)


@pytest.mark.parametrize('bad', [None, 'True', 1, [], {}])
def test_assist_parameter_requires_explicit_bool(bad):
    with pytest.raises(ValueError, match='must be bool'):
        run_live_case(*pair(), FakeClient(), policy_tool_assist=bad)


def test_diagnostic_cli_needs_live_consent():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


def test_diagnostic_cli_fake_provider_and_safe_output(capsys):
    case, f = pair(0)
    def fake_factory(provider):
        assert provider == 'gemini'
        return FakeClient()
    def fake_runner(c, fixture, client, **kwargs):
        assert c == case and fixture == f
        assert kwargs['policy_tool_assist'] is True
        assert kwargs['policy_passage_variant'] == 'clarified_v1'
        return {
            'publishable': False, 'reason_codes': ['POLICY_MISMATCH'],
            'next_action': 'revise_response', 'tool_rounds': 1,
            'tool_evidence': [{'tool_name': 'get_history'},
                              {'tool_name': 'retrieve_policy'},
                              {'tool_name': 'evaluate_policy_signals'}],
            'verification': {'schema': {'status':'passed'},
                             'numeric': {'status':'passed'},
                             'policy': {'status':'failed', 'checks': []}},
        }
    assert main(['--confirm-live-api'], client_factory=fake_factory, case_runner=fake_runner) == 0
    printed = capsys.readouterr().out
    assert 'Policy helper called: True' in printed
    assert 'BLOCK' in printed
    assert 'Provider: gemini' in printed
    assert '2024-12' not in printed
