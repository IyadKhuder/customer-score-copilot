"""Module 6.4.5: six-case policy-assisted benchmark, positive and negative tests."""
from __future__ import annotations

import copy
import json
import sys
import types
from dataclasses import dataclass
from unittest.mock import patch

import pytest

import src
from src.evaluation_dataset import load_benchmark
from src.evaluation_metrics import score_report
from src.evaluation_runner import load_fixtures
from src.live_evaluation import run_live_case
from src.policy_assist_suite import (
    SUITE_PROMPT_APPENDICES, SUITE_TOOL_SCHEMA, build_suite_assist_dispatch,
)
from scripts.run_assisted_benchmark_645 import main, run_assisted_benchmark


@pytest.fixture
def bundle():
    bench = load_benchmark()
    source = load_fixtures(benchmark=bench)
    by_id = {x['scenario_id']: x for x in source['fixtures']}
    return [(copy.deepcopy(c), copy.deepcopy(by_id[c['scenario_id']])) for c in bench['cases']]


def make_tool(case, fixture):
    req = case['request']
    rows = [{'period': p, 'score': s, 'bin': bin_number, 'market': req['market']}
            for p, s, bin_number in zip(fixture['periods'], fixture['scores'], fixture['bins'])]
    return build_suite_assist_dispatch(
        rows, account_id=req['account_id'], as_of_period=req['as_of_period'],
        market=req['market'],
        site_onboarded_period=req.get('site_onboarded_period'),
        last_exception_review_period=req.get('last_exception_review_period'),
    )


@pytest.mark.parametrize('index,expected_status,expected_events,mode,threshold,review', [
    (0, 'triggered', 1, 'standard', 3, 'not_applicable'),
    (1, 'not_triggered', 0, 'standard', 3, 'not_applicable'),
    (2, 'pending_data_quality', 2, 'standard', 3, 'not_applicable'),
    (3, 'insufficient_evidence', 0, 'standard', 3, 'not_applicable'),
    (4, 'triggered', 1, 'grace', 4, 'current'),
    (5, 'pending_data_quality', 1, 'grace', 4, 'current'),
])
def test_exact_six_scenario_policy_outputs(bundle, index, expected_status, expected_events, mode, threshold, review):
    case, fixture = bundle[index]
    req = case['request']
    result = make_tool(case, fixture)(account_id=req['account_id'], as_of_period=req['as_of_period'])
    applicability = result['policy_applicability']
    assert result['warning_signal_status'] == expected_status
    assert result['consecutive_decline_event_count'] == expected_events
    assert applicability['mode'] == mode
    assert applicability['thresholds']['consecutive_declines'] == threshold
    assert applicability['quarterly_exception_review'] == review
    assert len(result['consecutive_decline_events']) == expected_events
    assert result['market'] == req['market']


def test_negative_case_explicit_insufficient_evidence(bundle):
    case, f = bundle[3]
    result = make_tool(case, f)(account_id=case['request']['account_id'], as_of_period='1991-12-01')
    assert result['warning_signal_status'] == 'insufficient_evidence'
    assert result['consecutive_decline_event_count'] == 0
    assert result['coverage_complete'] is False
    assert result['missing_baseline_months']


@pytest.mark.parametrize('idx', list(range(6)))
def test_cross_account_mismatch_rejected(bundle, idx):
    case, fixture = bundle[idx]
    fn = make_tool(case, fixture)
    with pytest.raises(ValueError, match='context mismatch'):
        fn(account_id=case['request']['account_id'] + 1,
           as_of_period=case['request']['as_of_period'])


@pytest.mark.parametrize('idx', list(range(6)))
def test_wrong_date_mismatch_rejected(bundle, idx):
    case, fixture = bundle[idx]
    fn = make_tool(case, fixture)
    with pytest.raises(ValueError, match='context mismatch'):
        fn(account_id=case['request']['account_id'], as_of_period='1991-11-01')


@pytest.mark.parametrize('bad', [True, '81000001', None, 81000001.0])
def test_non_integer_account_rejected(bundle, bad):
    case, f = bundle[0]
    with pytest.raises(ValueError):
        make_tool(case, f)(account_id=bad, as_of_period='1991-12-01')


def test_grace_requires_trusted_caller_metadata(bundle):
    case, fixture = bundle[4]
    req = case['request']
    rows = [dict(period=p, score=s, bin=bin_number, market=3)
            for p, s, bin_number in zip(fixture['periods'], fixture['scores'], fixture['bins'])]
    with pytest.raises(ValueError):
        build_suite_assist_dispatch(rows, account_id=83000005,
                                    as_of_period='1991-12-01', market=3)
    with pytest.raises(ValueError):
        build_suite_assist_dispatch(rows, account_id=83000005,
                                    as_of_period='1991-12-01', market=3,
                                    site_onboarded_period='2025-09-01',
                                    last_exception_review_period='1991-12-01')


def test_bound_history_immune_to_external_mutation(bundle):
    case, fixture = bundle[0]
    req = case['request']
    rows = [dict(period=p, score=s, bin=bin_number, market=1)
            for p, s, bin_number in zip(fixture['periods'], fixture['scores'], fixture['bins'])]
    fn = build_suite_assist_dispatch(rows, account_id=81000001,
                                     as_of_period='1991-12-01', market=1)
    before = fn(account_id=81000001, as_of_period='1991-12-01')
    rows[2]['score'] = -1000
    after = fn(account_id=81000001, as_of_period='1991-12-01')
    assert before == after
    after['missing_baseline_months'].append('2050-01-01')
    assert fn(account_id=81000001, as_of_period='1991-12-01') == before


def test_tool_and_prompts_do_not_include_benchmark_answers():
    assert SUITE_TOOL_SCHEMA['name'] == 'evaluate_policy_signals'
    assert SUITE_TOOL_SCHEMA['input_schema']['required'] == ['account_id', 'as_of_period']
    text = json.dumps(SUITE_TOOL_SCHEMA) + str(SUITE_PROMPT_APPENDICES)
    for forbidden in ('CF-001', 'CF-004', '81000001', '83000005', '1991-11-01'):
        assert forbidden not in text
    assert len(SUITE_PROMPT_APPENDICES) == 4
    assert 'insufficient_evidence' in SUITE_PROMPT_APPENDICES['score_bin_warning_signal_quality_hold']


class DummyClient:
    def chat(self, *, messages, tools):
        pass
    def continue_with_tool_results(self, *, assistant_response, tool_results, tools):
        pass


@pytest.mark.parametrize('idx,attr', [
    (0, 'CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS'),
    (1, 'CLAIM_FIRST_NO_DECLINES_INSTRUCTIONS'),
    (2, 'CLAIM_FIRST_QUALITY_HOLD_INSTRUCTIONS'),
    (3, 'CLAIM_FIRST_QUALITY_HOLD_INSTRUCTIONS'),
    (4, 'CLAIM_FIRST_GRACE_DECLINES_INSTRUCTIONS'),
    (5, 'CLAIM_FIRST_GRACE_DECLINES_INSTRUCTIONS'),
])
def test_six_scopes_dispatch_and_restore(bundle, idx, attr):
    case, fixture = bundle[idx]
    agent = types.ModuleType('src.agent')
    agent.ALL_TOOLS = [{'name':'get_history'}, {'name':'retrieve_policy'}, {'name':'other'}]
    old = lambda **kwargs: 123
    agent.ALL_TOOL_DISPATCH = {'other':old}
    originals = {}
    for field in set(['CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS',
                      'CLAIM_FIRST_NO_DECLINES_INSTRUCTIONS',
                      'CLAIM_FIRST_QUALITY_HOLD_INSTRUCTIONS',
                      'CLAIM_FIRST_GRACE_DECLINES_INSTRUCTIONS']):
        setattr(agent, field, field + '_ORIGINAL')
        originals[field] = getattr(agent, field)
    def run_agent(question, llm, **kwargs):
        assert {x['name'] for x in agent.ALL_TOOLS} == {
            'get_history','retrieve_policy','evaluate_policy_signals'}
        assert set(agent.ALL_TOOL_DISPATCH) == {
            'get_history','retrieve_policy','evaluate_policy_signals'}
        assert SUITE_PROMPT_APPENDICES[case['request']['claim_scope']] in getattr(agent, attr)
        result = agent.ALL_TOOL_DISPATCH['evaluate_policy_signals'](
            account_id=case['request']['account_id'], as_of_period='1991-12-01')
        if idx>=4:
            assert kwargs['site_onboarded_period'] == '1991-01-01'
            assert result['policy_applicability']['mode'] == 'grace'
        return {'publishable':True, 'status':'passed', 'next_action':'publish',
                'reason_codes':[], 'tool_evidence':[], 'completed':True,
                'tool_rounds':1, 'answer':'OK'}
    agent.run_agent = run_agent
    with patch.dict(sys.modules, {'src.agent':agent}), patch.object(src,'agent',agent,create=True):
        result = run_live_case(case, fixture, DummyClient(), policy_tool_suite=True)
    assert result['publishable']
    assert agent.ALL_TOOL_DISPATCH == {'other':old}
    assert agent.ALL_TOOLS == [{'name':'get_history'},{'name':'retrieve_policy'},{'name':'other'}]
    assert all(getattr(agent, field)==value for field, value in originals.items())


def test_original_cf001_pilot_does_not_change(bundle):
    case, fixture = bundle[4]
    with pytest.raises(ValueError, match='supports only standard-market full reports'):
        run_live_case(case, fixture, DummyClient(), policy_tool_assist=True)


@pytest.mark.parametrize('kwargs', [
    {'policy_tool_suite':1}, {'policy_tool_suite':'True'},
    {'policy_tool_suite':True,'policy_tool_assist':True},
])
def test_invalid_or_both_modes_rejected(bundle, kwargs):
    with pytest.raises(ValueError):
        run_live_case(*bundle[0], DummyClient(), **kwargs)


@pytest.mark.parametrize('idx', [0,1,2,3,4,5])
def test_unknown_tool_does_not_reach_real_dispatch(bundle, idx):
    case, fixture = bundle[idx]
    agent = types.ModuleType('src.agent')
    agent.ALL_TOOLS = [{'name':'get_history'}, {'name':'retrieve_policy'},
                       {'name':'get_latest_score'}]
    used = []
    agent.ALL_TOOL_DISPATCH = {'get_latest_score':lambda **_:used.append(1)}
    field = {0:'CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS',
             1:'CLAIM_FIRST_NO_DECLINES_INSTRUCTIONS',
             2:'CLAIM_FIRST_QUALITY_HOLD_INSTRUCTIONS',
             3:'CLAIM_FIRST_QUALITY_HOLD_INSTRUCTIONS',
             4:'CLAIM_FIRST_GRACE_DECLINES_INSTRUCTIONS',
             5:'CLAIM_FIRST_GRACE_DECLINES_INSTRUCTIONS'}[idx]
    setattr(agent, field, 'PROMPT')
    def run_agent(*args, **kwargs):
        assert 'get_latest_score' not in agent.ALL_TOOL_DISPATCH
        assert 'get_latest_score' not in [t['name'] for t in agent.ALL_TOOLS]
        return {'answer':'', 'publishable':False}
    agent.run_agent=run_agent
    with patch.dict(sys.modules, {'src.agent':agent}), patch.object(src,'agent',agent,create=True):
        run_live_case(case, fixture, DummyClient(), policy_tool_suite=True)
    assert used == []


def test_suite_requires_provider_adapter():
    with pytest.raises(TypeError):
        run_assisted_benchmark(None, provider_id='gemini.assisted')


@pytest.mark.parametrize('bad', ['', 'contains space', '@wrong', 'a'*65])
def test_invalid_provider_labels_rejected(bad):
    with pytest.raises(ValueError):
        run_assisted_benchmark(DummyClient(), provider_id=bad)


def test_cli_requires_consent(tmp_path):
    with pytest.raises(SystemExit) as e:
        main(['--provider','gemini','--model-label','label', '--output', str(tmp_path/'x.json')])
    assert e.value.code != 0
    assert not (tmp_path/'x.json').exists()


def test_cli_refuses_overwrite(tmp_path):
    p=tmp_path/'existing.json';p.write_text('{}')
    with pytest.raises(SystemExit):
        main(['--provider','gemini','--model-label','label','--output',str(p), '--confirm-live-api'])
    assert p.read_text() == '{}'


def test_cli_creates_safe_report_and_prints_no_provider_draft(tmp_path, capsys):
    p=tmp_path/'report.json'
    def fake_runner(client, **kwargs):
        assert kwargs['policy_passage_variant'] == 'clarified_v1'
        return {
            'benchmark_id':'benchmark','cases':[
                {'case_id':'CF-001','error_type':None,'elapsed_ms':123.0,
                 'observed':{'publishable':True,'tool_names':['evaluate_policy_signals']}}
            ]}
    rc=main(['--provider','gemini','--model-label','gemini-3.5-flash-lite',
             '--output',str(p), '--confirm-live-api'],
             client_factory=lambda name: DummyClient(),benchmark_runner=fake_runner)
    assert rc == 0
    assert p.exists()
    out=capsys.readouterr().out
    assert 'helper called: True' in out
    assert 'CF-001' in out
    assert 'SECRET_DRAFT' not in out


# These tests use a tool-aware SCRIPTED provider to exercise the complete
# actual Module 5 verification/composition/gate. NOT a live-model quality test.
@dataclass
class _Call:
    id: str
    name: str
    arguments: dict

@dataclass
class _Response:
    text: str = ''
    tool_calls: list | None = None

class _ToolAwareScriptedProvider:
    def __init__(self):
        self.request = None
        self.scope = None
    def chat(self, *, messages, tools):
        self.request = next(t for t in tools if t['name']=='evaluate_policy_signals')
        content = messages[0]['content']
        # Extract context from caller prompt, not any benchmark expected fields.
        import re
        m = re.search(r'account_id=(\d+), as_of_period=(\d{4}-\d\d-\d\d), market=(\d+)',content)
        assert m
        self.account, self.period, self.market = int(m[1]), m[2], int(m[3])
        for scope, token in [
            ('grace_decline_events','POL-004 GRACE-MARKET DECLINE-EVENT report'),
            ('score_bin_warning_signal_quality_hold','DATA-QUALITY STATUS report'),
            ('score_bin_warning_signal_no_declines','verified NO-DECLINE report'),
            ('score_bin_warning_signal_decline_events','FULL COMBINED score'),
        ]:
            if token in content:
                self.scope=scope;break
        assert self.scope
        return _Response(tool_calls=[
            _Call('one','get_history',{'account_id':self.account}),
            _Call('two','retrieve_policy',{'query':'POL-002 POL-004 POL-005','k':3}),
            _Call('three','evaluate_policy_signals',{'account_id':self.account,
                                                     'as_of_period':self.period}),
        ])
    def continue_with_tool_results(self, *, assistant_response, tool_results, tools):
        rows, docs, policy = tool_results
        if self.scope=='grace_decline_events':
            app=policy['policy_applicability']
            vals={
                'policy_applicability.mode':app['mode'],
                'policy_applicability.thresholds.consecutive_declines':app['thresholds']['consecutive_declines'],
                'policy_applicability.quarterly_exception_review':app['quarterly_exception_review'],
                'raw_event_count.consecutive_declines':policy['consecutive_decline_event_count'],
            }
            score_claims=[]
        else:
            vals={'warning_signal_status':policy['warning_signal_status'],
                  'raw_event_count.consecutive_declines':policy['consecutive_decline_event_count']}
            score_claims=[{'account_id':self.account,'period':self.period,'market':self.market,
                           'metric':metric,'value':rows[-1][metric]}
                          for metric in ('score','bin')]
        claims=[{'account_id':self.account,'as_of_period':self.period,'market':self.market,
                 'field':k,'value':v} for k,v in vals.items()]
        return _Response(text=json.dumps({'answer':'UNTRUSTED DRAFT: do not publish',
                                          'score_claims':score_claims,'policy_claims':claims,
                                          'limitations':[]}),tool_calls=[])


def test_six_case_scripted_agent_replay_through_real_verifier():
    import importlib.util
    if importlib.util.find_spec("src.guardrail") is None:
        pytest.fail("Required real-verifier integration module src.guardrail is missing")
    run = run_assisted_benchmark(_ToolAwareScriptedProvider(),provider_id='simulated.assisted')
    assert len(run['cases'])==6
    score = score_report(run)
    assert score['metrics']['cases_passed_all_checks'] == 6
    assert score['metrics']['publication_decision_accuracy'] == 1.0
    assert score['metrics']['correct_blocks'] == 1
    assert [x['observed']['publishable'] for x in run['cases']] == [True,True,True,False,True,True]
    for row in run['cases']:
        assert 'evaluate_policy_signals' in row['observed']['tool_names']
        assert 'UNTRUSTED DRAFT' not in row['observed']['public_answer']
        assert row['error_type'] is None
    neg=run['cases'][3]['observed']
    assert neg['reason_codes'] == ['POLICY_EVIDENCE_INSUFFICIENT']
    assert neg['next_action'] == 'gather_evidence'
