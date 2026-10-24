"""Module 5.5.6.5: full narrative composer + response gate tests, offline.

Policy decision calculations and the score tool are test doubles; the real
verification pipeline, composers, temporal mapping and agent control flow run.
"""
import copy
import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from src import agent
from src.combined_temporal_narrative import (
    CombinedTemporalNarrativeError, compose_verified_full_narrative,
)

ACCOUNT, PERIOD, MARKET = 81000001, "2025-04-01", 1
PERIODS = ["2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01", PERIOD]
VALUES = [286, 282, 275, 267, 271]
ROWS = [dict(period=d, score=s, bin=6, market=1) for d, s in zip(PERIODS, VALUES)]
PASSAGE = [{"source":"POL-002_early_warning_thresholds.md", "text":"Three consecutive declines."}]


def payload():
    return {
        "answer": "UNTRUSTED: the account has a registered warning and must be denied!",
        "score_claims": [
            {"account_id":ACCOUNT, "period":PERIOD,"metric":"score","value":271,"market":MARKET},
            {"account_id":ACCOUNT, "period":PERIOD,"metric":"bin","value":6,"market":MARKET},
        ],
        "policy_claims": [
            {"field":"warning_signal_status", "value":"triggered",
             "account_id":ACCOUNT, "as_of_period":PERIOD, "market":MARKET},
            {"field":"raw_event_count.consecutive_declines", "value":1,
             "account_id":ACCOUNT, "as_of_period":PERIOD, "market":MARKET},
        ],
        "limitations": [],
    }


def event_record():
    return {"criterion":"consecutive_declines", "period":"2025-03-01",
            "evidence_periods": PERIODS[:4], "review_state":"eligible"}


def evidence_record():
    return {"window_start":"2025-02-01", "window_end":PERIOD,
            "events":[event_record()], "data_quality_flags":[],
            "policy_sources":["POL-002","POL-005"]}


def inner_audit(claim):
    return {"status":"passed", "account_id":ACCOUNT, "as_of_period":PERIOD,
            "market":MARKET, "unchecked_claims":[],
            "checks":[{"field":claim["field"], "claimed":claim["value"],
                       "expected":claim["value"], "context_matches":True,
                       "value_matches":True,"passed":True}],
            "evidence":evidence_record()}


def passed_audit():
    data = payload()
    return {"status":"passed", "schema":{"status":"passed"},
            "answer":data["answer"], "limitations":[],"issues":[],
            "narrative":{"coverage_method":"llm_draft_excluded"},
            "numeric":{"status":"passed","checks":[
                {"status":"passed","claim":c,"context_grounded":True,"value_grounded":True}
                for c in data["score_claims"]]},
            "policy":{"status":"passed","checks":[
                {"status":"passed", "claim":c, "audit":inner_audit(c)}
                for c in data["policy_claims"]]}}


def tools():
    return [{"tool_name":"get_history","arguments":{"account_id":ACCOUNT},
             "result":copy.deepcopy(ROWS)}]


def render(audit=None, tool_evidence=None, **kwargs):
    return compose_verified_full_narrative(
        passed_audit() if audit is None else audit,
        tools() if tool_evidence is None else tool_evidence,
        **(kwargs or {"account_id":ACCOUNT,"as_of_period":PERIOD,"market":MARKET}),
    )


def test_composer_renders_all_four_claims_and_never_model_draft():
    result=render()
    assert 'the verified score was 271 and the risk bin was 6' in result
    assert 'verified early-warning signal' in result
    assert 'POL-002 evaluation window' in result
    assert '2024-12-01 through 2025-03-01' in result
    assert 'eligible for early-warning consideration' in result
    assert 'operational warning was registered or cleared' in result
    assert 'UNTRUSTED' not in result and 'must be denied' not in result


def test_claim_and_event_order_independent():
    a=passed_audit();a['numeric']['checks'].reverse();a['policy']['checks'].reverse()
    assert render(a)==render()


def test_draft_variations_never_change_text():
    a=passed_audit();a['answer']='Unverified false claim 20000 point drop!'
    assert render(a)==render()


@pytest.mark.parametrize('edit', [
    lambda a:a.update(status='failed'),
    lambda a:a['schema'].update(status='incomplete'),
    lambda a:a['narrative'].update(coverage_method='legacy_scan'),
    lambda a:a['issues'].append('Policy check incomplete'),
    lambda a:a['limitations'].append('Missing register'),
    lambda a:a['numeric'].update(status='incomplete'),
    lambda a:a['policy'].update(status='failed'),
    lambda a:a['numeric']['checks'].pop(),
    lambda a:a['numeric']['checks'].append(copy.deepcopy(a['numeric']['checks'][0])),
    lambda a:a['policy']['checks'].pop(),
    lambda a:a['policy']['checks'].append(copy.deepcopy(a['policy']['checks'][0])),
    lambda a:a['policy']['checks'][0]['claim'].update(field='analyst_referral'),
    lambda a:a['policy']['checks'][0].update(status='failed'),
    lambda a:a['numeric']['checks'][0].update(context_grounded=False),
    lambda a:a['numeric']['checks'][0]['claim'].update(period='2025-03-01'),
    lambda a:a['policy']['checks'][0]['claim'].update(as_of_period='2025-03-01'),
    lambda a:a['policy']['checks'][0]['claim'].update(market=2),
    lambda a:a['policy']['checks'][0]['audit']['checks'][0].update(expected='not_triggered'),
    lambda a:a['policy']['checks'][1]['audit']['checks'][0].update(value_matches=False),
    lambda a:a['policy']['checks'][1]['audit']['evidence'].update(window_start='2025-01-01'),
    lambda a:a['policy']['checks'][0]['audit']['evidence']['events'][0].update(period='2025-02-01'),
    lambda a:a['policy']['checks'][1]['audit']['evidence']['events'][0].update(period='2025-02-01'),
    lambda a:a['policy']['checks'][1]['audit']['evidence']['events'][0].update(review_state='pending_data_quality'),
])
def test_composer_rejects_unverified_missing_or_inconsistent_context(edit):
    audit=passed_audit();edit(audit)
    with pytest.raises(CombinedTemporalNarrativeError): render(audit)


@pytest.mark.parametrize('kwargs',[
    {'account_id':ACCOUNT+1,'as_of_period':PERIOD,'market':MARKET},
    {'account_id':ACCOUNT,'as_of_period':'2025-03-01','market':MARKET},
    {'account_id':ACCOUNT,'as_of_period':PERIOD,'market':2},
    {'account_id':ACCOUNT,'as_of_period':PERIOD,'market':'1'},
])
def test_wrong_caller_context_blocks(kwargs):
    with pytest.raises(CombinedTemporalNarrativeError):render(**kwargs)


def test_conflicting_raw_histories_block():
    ev=tools();other=copy.deepcopy(ev[0]);other['result'][-1]['score']=401;ev.append(other)
    with pytest.raises(CombinedTemporalNarrativeError):render(tool_evidence=ev)


def test_zero_count_even_if_consistently_fake_audit_blocks():
    a=passed_audit()
    c=a['policy']['checks'][1]
    c['claim']['value']=0;c['audit']['checks'][0].update(claimed=0,expected=0)
    c['audit']['evidence']['events']=[]
    a['policy']['checks'][0]['audit']['evidence']['events']=[]
    with pytest.raises(CombinedTemporalNarrativeError):render(a)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class Reply:
    text: str = ''
    tool_calls: list = field(default_factory=list)

class Scripted:
    def __init__(self,*replies):
        self.replies=list(replies)
        self.initial_messages=None
    def chat(self,messages,tools):
        self.initial_messages=messages
        return self.replies.pop(0)
    def continue_with_tool_results(self,assistant_response,tool_results,tools):
        return self.replies.pop(0)


def fake_policy_audit(claims, history, *, account_id, as_of_period):
    # Lightweight checker for the *integration plumbing*, not policy simulation.
    # The separately tested real policy_decisions module is authoritative.
    claim=claims[0]
    if claim['field']=='warning_signal_status':expected='triggered'
    elif claim['field']=='raw_event_count.consecutive_declines':expected=1
    else:expected=None
    history_ok=(len(history)==len(ROWS) and
                [r['score'] for r in history]==VALUES)
    context_ok=(claim['account_id']==account_id==ACCOUNT
                and claim['as_of_period']==as_of_period==PERIOD
                and claim['market']==MARKET)
    value_ok=(type(claim['value']) is type(expected) and claim['value']==expected)
    ok=history_ok and context_ok and value_ok and expected is not None
    return {'status':'passed' if ok else 'failed',
            'account_id':account_id, 'as_of_period':as_of_period,'market':MARKET,
            'unchecked_claims':[],
            'checks':[{'field':claim['field'],'claimed':claim['value'],'expected':expected,
                       'context_matches':context_ok,'value_matches':value_ok,'passed':ok}],
            'evidence':evidence_record()}


@pytest.fixture(autouse=True)
def fake_guard_and_tools():
    guard=types.ModuleType('src.guardrail')
    @dataclass(frozen=True)
    class ScoreClaim:
        account_id: int
        period: str
        metric: str
        value: int
        market: int
    guard.ScoreClaim=ScoreClaim
    def check_score_claim(c, evidence):
        rows=[r for item in evidence if item.get('tool_name')=='get_history'
              and item['arguments'].get('account_id')==c.account_id
              for r in item['result']]
        ok=any(row['period']==c.period and row['market']==c.market
               and row[c.metric]==c.value for row in rows)
        return {'value_grounded':ok,'context_grounded':ok}
    guard.check_score_claim=check_score_claim
    guard.audit_answer=guard.extract_score_claims=lambda *_: (_ for _ in ()).throw(
        AssertionError('legacy free-text checker was called'))
    policy=types.ModuleType('src.policy_claim_guard')
    policy.audit_policy_claims=fake_policy_audit
    with patch.dict(sys.modules, {'src.guardrail':guard,'src.policy_claim_guard':policy}),\
         patch.dict(agent.ALL_TOOL_DISPATCH, {'get_history':lambda account_id:copy.deepcopy(ROWS) if account_id==ACCOUNT else [],
                                              'retrieve_policy':lambda **kwargs:copy.deepcopy(PASSAGE)}):
        yield


def run_case(data=None, *, history=True, retrieval=True, account=ACCOUNT,
             period=PERIOD, market=MARKET, scope='score_bin_warning_signal_decline_events'):
    calls=[]
    if history:calls.append(ToolCall('h','get_history',{'account_id':ACCOUNT}))
    if retrieval:calls.append(ToolCall('p','retrieve_policy',{'query':'POL-002 3 declines'}))
    responses=[Reply(tool_calls=calls), Reply(text=json.dumps(payload() if data is None else data))] if calls else [Reply(text=json.dumps(payload() if data is None else data))]
    model=Scripted(*responses)
    out=agent.run_agent('Give verified scores policy status and event history',model,
        publication_mode='claims_first',claim_scope=scope,
        expected_account_id=account,expected_period=period,expected_market=market,
        verbose=False)
    return out,model


def test_agent_publishes_full_report_through_gate():
    result,model=run_case()
    assert result['publishable'] is True,result
    assert result['status']=='passed' and result['next_action']=='publish'
    assert result['verification']['rendering']=={'status':'passed','method':'verified_full_temporal_narrative'}
    assert result['verification']['narrative']['status']=='not_applicable'
    assert 'verified score was 271' in result['answer']
    assert 'verified early-warning signal' in result['answer']
    assert 'POL-002 evaluation window' in result['answer']
    assert 'registered warning and must be denied' not in result['answer']
    prompt=model.initial_messages[0]['content']
    assert 'FULL COMBINED' in prompt
    assert f'account_id={ACCOUNT}, as_of_period={PERIOD}, market={MARKET}' in prompt


def test_agent_ignores_llm_prose_entirely():
    d=payload(); d['answer']='Fabricated 9000 score loss and instant committee action.'
    a,_=run_case(d); b,_=run_case()
    assert a['publishable'] and a['answer']==b['answer']
    assert '9000' not in a['answer']


@pytest.mark.parametrize('edit',[
    lambda d:d['score_claims'].pop(),
    lambda d:d['score_claims'].append(copy.deepcopy(d['score_claims'][0])),
    lambda d:d['score_claims'][0].update(value=999),
    lambda d:d['score_claims'][0].update(period='2025-03-01'),
    lambda d:d['policy_claims'].pop(),
    lambda d:d['policy_claims'].append(copy.deepcopy(d['policy_claims'][0])),
    lambda d:d['policy_claims'][0].update(value='not_triggered'),
    lambda d:d['policy_claims'][1].update(value=0),
    lambda d:d['policy_claims'][1].update(value=2),
    lambda d:d['policy_claims'][1].update(field='analyst_referral'),
    lambda d:d['policy_claims'][1].update(as_of_period='2025-03-01'),
    lambda d:d['policy_claims'][0].update(market=3),
    lambda d:d['limitations'].append('Unknown register'),
])
def test_agent_blocks_missing_extra_inconsistent_claims(edit):
    d=payload();edit(d)
    out,_=run_case(d)
    assert not out['publishable'],out
    assert 'UNTRUSTED' not in out['answer']


@pytest.mark.parametrize('kwargs',[
    {'history':False}, {'retrieval':False}, {'account':ACCOUNT+1},
    {'period':'2025-03-01'},
])
def test_agent_blocks_missing_evidence_or_wrong_context(kwargs):
    out,_=run_case(**kwargs)
    assert not out['publishable'],out


def test_retrieval_from_unrelated_policy_does_not_authorize_full_report():
    with patch.dict(agent.ALL_TOOL_DISPATCH, {'retrieve_policy': lambda **kwargs:[
        {'source':'POL-003_actions.md','text':'Actions'}]}):
        out,_=run_case()
    assert not out['publishable']
    assert 'POLICY_RETRIEVAL_MISSING' in out['reason_codes']


def test_empty_retrieval_does_not_authorize_full_report():
    with patch.dict(agent.ALL_TOOL_DISPATCH, {'retrieve_policy':lambda **kwargs:[]}):
        out,_=run_case()
    assert not out['publishable']
    assert 'POLICY_RETRIEVAL_MISSING' in out['reason_codes']


@pytest.mark.parametrize('market',[2,3,None,True])
def test_nonstandard_or_invalid_market_is_rejected_before_llm(market):
    with pytest.raises(ValueError):run_case(market=market)


def test_no_historical_event_blocks_even_with_model_claims():
    increasing=[dict(row,score=600+i) for i,row in enumerate(ROWS)]
    with patch.dict(agent.ALL_TOOL_DISPATCH, {'get_history':lambda account_id:increasing}):
        out,_=run_case()
    assert not out['publishable']


def test_composition_error_yields_safe_fallback():
    with patch.object(agent,'compose_verified_full_narrative',side_effect=CombinedTemporalNarrativeError('no evidence')):
        out,_=run_case()
    assert not out['publishable']
    assert 'COMPOSITION_UNSUPPORTED' in out['reason_codes']
    assert 'UNTRUSTED' not in out['answer']


def test_verifier_exception_blocks():
    with patch.object(agent,'verify_structured_response',side_effect=RuntimeError('broken')):
        out,_=run_case()
    assert not out['publishable']
    assert 'VERIFICATION_ERROR' in out['reason_codes']


def test_new_scope_cannot_be_used_in_legacy_mode():
    with pytest.raises(ValueError):
        agent.run_agent('x',Scripted(Reply(text='{}')),verbose=False,
                        publication_mode='legacy',claim_scope='score_bin_warning_signal_decline_events')
