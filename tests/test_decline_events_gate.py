"""Module 5.5.6.4: temporal narrative agent/gate integration (offline).

The production verifier, event mapper, temporal renderer and agent run here.
The policy claim auditor is a deterministic stand-in for the separate policy
engine; policy decisions themselves remain covered by the project's tests.
"""

import copy
import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest
from src import agent

ACCOUNT, AS_OF, MARKET = 81000001, "2025-04-01", 1
DATES = ["2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01", AS_OF]
SCORES = [286, 282, 275, 267, 271]
ROWS = [dict(period=d, score=s, bin=6, market=MARKET) for d, s in zip(DATES, SCORES)]
PASSAGE = [{"source": "POL-002_early_warning_thresholds.md",
            "heading": "Early-Warning Thresholds",
            "text": "Three consecutive monthly declines qualify for early-warning consideration."}]


def payload(count=1):
    return {
        "answer": "UNTRUSTED LLM prose: operational warning registered, auto-decline credit!",
        "score_claims": [],
        "policy_claims": [{"field": "raw_event_count.consecutive_declines", "value": count,
                           "account_id": ACCOUNT, "as_of_period": AS_OF, "market": MARKET}],
        "limitations": [],
    }


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.first_messages = None

    def chat(self, messages, tools):
        self.first_messages = messages
        return self.replies.pop(0)

    def continue_with_tool_results(self, assistant_response, tool_results, tools):
        return self.replies.pop(0)


def fake_policy_audit(claims, history, *, account_id, as_of_period):
    """Small independent decline check for agent plumbing, not policy calibration."""
    from datetime import date
    def m(d):
        x = date.fromisoformat(d)
        return x.year * 12 + x.month
    eligible_events = []
    for i in range(3, len(history)):
        obs = history[i-3:i+1]
        if (all(m(b['period']) - m(a['period']) == 1 for a,b in zip(obs,obs[1:]))
                and all(a['score'] > b['score'] for a,b in zip(obs,obs[1:]))
                and "2025-02-01" <= obs[-1]['period'] <= as_of_period):
            eligible_events.append({"criterion":"consecutive_declines",
                                    "period":obs[-1]['period'],
                                    "evidence_periods":[r['period'] for r in obs],
                                    "review_state":"eligible"})
    claim=claims[0]
    expected=len(eligible_events)
    context_ok=claim['account_id']==account_id and claim['as_of_period']==as_of_period and claim['market']==1
    value_ok=type(claim['value']) is int and claim['value']==expected
    ok=context_ok and value_ok and claim['field']=="raw_event_count.consecutive_declines"
    return {
        "status":"passed" if ok else "failed", "account_id":account_id,
        "as_of_period":as_of_period, "market":MARKET, "unchecked_claims":[],
        "checks":[{"field":claim['field'], "claimed":claim['value'],"expected":expected,
                   "context_matches":context_ok,"value_matches":value_ok,"passed":ok}],
        "evidence":{"window_start":"2025-02-01","window_end":as_of_period,
                    "events":eligible_events,"data_quality_flags":[],
                    "policy_sources":["POL-002","POL-005"]},
    }


@pytest.fixture(autouse=True)
def fake_external_policy_and_tool_calls():
    guard=types.ModuleType('src.guardrail')
    @dataclass(frozen=True)
    class ScoreClaim:
        account_id: int
        period: str
        metric: str
        value: int
        market: int
    guard.ScoreClaim=ScoreClaim
    def score_check(c, ev):
        matching = [row for item in ev if item.get('tool_name')=='get_history' and item['arguments'].get('account_id')==c.account_id for row in item['result']]
        ok=any(row['period']==c.period and row['market']==c.market and row[c.metric]==c.value for row in matching)
        return {'value_grounded':ok,'context_grounded':ok}
    guard.check_score_claim=score_check
    guard.audit_answer=guard.extract_score_claims=lambda *_: (_ for _ in ()).throw(AssertionError('legacy prose scanner called'))
    policy=types.ModuleType('src.policy_claim_guard')
    policy.audit_policy_claims=fake_policy_audit
    with patch.dict(sys.modules, {'src.guardrail':guard,'src.policy_claim_guard':policy}), patch.dict(agent.ALL_TOOL_DISPATCH, {'get_history':lambda account_id: copy.deepcopy(ROWS) if account_id==ACCOUNT else [], 'retrieve_policy':lambda **kwargs:copy.deepcopy(PASSAGE)}):
        yield


def run_case(data=None, *, history=True, retrieval=True, account=ACCOUNT, date=AS_OF, market=MARKET,
             scope='decline_events', extra_history=False):
    calls=[]
    if history:
        calls.append(ToolCall('h1','get_history',{'account_id':ACCOUNT}))
        if extra_history:
            calls.append(ToolCall('h2','get_history',{'account_id':ACCOUNT}))
    if retrieval:
        calls.append(ToolCall('p1','retrieve_policy',{'query':'POL-002 three consecutive declines'}))
    reply=Reply(text=json.dumps(payload() if data is None else data))
    model=Scripted(Reply(tool_calls=calls),reply) if calls else Scripted(reply)
    result=agent.run_agent('Explain the decline events',model,verbose=False,
        publication_mode='claims_first',claim_scope=scope, expected_account_id=account,
        expected_period=date,expected_market=market)
    return result,model


def test_publishes_dated_eligible_event_with_policy_and_window():
    result,model=run_case()
    assert result['publishable'] is True, result
    assert result['status']=='passed'
    assert result['next_action']=='publish'
    assert result['verification']['rendering']=={'status':'passed','method':'verified_decline_events'}
    answer=result['answer']
    assert 'POL-002 evaluation window' in answer
    assert '2024-12-01 through 2025-03-01' in answer
    assert 'eligible for early-warning consideration' in answer
    assert 'UNTRUSTED' not in answer and 'auto-decline' not in answer
    assert 'operational warning was registered or cleared' in answer
    assert result['verification']['narrative']['status']=='not_applicable'
    prompt=model.first_messages[0]['content']
    assert 'TEMPORAL-EVENT-ONLY' in prompt
    assert 'raw_event_count.consecutive_declines' in prompt
    assert f'account_id={ACCOUNT}, as_of_period={AS_OF}, market={MARKET}' in prompt


def test_ignores_fabricated_and_varying_llm_drafts():
    baseline,_=run_case()
    data=payload()
    data['answer']='Your credit will increase 20000 points today.'
    result,_=run_case(data)
    assert result['publishable'] is True
    assert result['answer']==baseline['answer']
    assert '20000' not in result['answer']


@pytest.mark.parametrize('mutate',[
    lambda d:d['policy_claims'].clear(),
    lambda d:d['policy_claims'][0].update(value=0),
    lambda d:d['policy_claims'][0].update(value=2),
    lambda d:d['policy_claims'][0].update(value=True),
    lambda d:d['policy_claims'][0].update(value='1'),
    lambda d:d['policy_claims'][0].update(field='warning_signal_status'),
    lambda d:d['policy_claims'][0].update(market=2),
    lambda d:d['policy_claims'][0].update(as_of_period='2025-03-01'),
    lambda d:d['policy_claims'].append(copy.deepcopy(d['policy_claims'][0])),
    lambda d:d['score_claims'].append({'account_id':ACCOUNT,'period':AS_OF,
                                      'market':MARKET,'metric':'score','value':271}),
    lambda d:d['limitations'].append('History incomplete'),
])
def test_unsupported_or_unverified_claims_fail_closed(mutate):
    d=payload()
    mutate(d)
    r,_=run_case(d)
    assert r['publishable'] is False, r
    assert 'UNTRUSTED' not in r['answer']


@pytest.mark.parametrize('kwargs',[
    {'history':False},
    {'retrieval':False},
    {'account':81000999},
    {'date':'2025-03-01'},
])
def test_missing_evidence_or_wrong_reporting_context_blocks(kwargs):
    r,_=run_case(**kwargs)
    assert r['publishable'] is False,r


def test_requires_pol002_passage_not_just_any_policy_hit():
    with patch.dict(agent.ALL_TOOL_DISPATCH,{'retrieve_policy':lambda **kwargs:[{'source':'POL-003_actions.md','text':'unrelated policy text'}]}):
        r,_=run_case()
    assert r['publishable'] is False
    assert 'POLICY_RETRIEVAL_MISSING' in r['reason_codes']


def test_empty_policy_retrieval_blocks_even_if_every_claim_passed():
    with patch.dict(agent.ALL_TOOL_DISPATCH,{'retrieve_policy':lambda **kwargs:[]}):
        r,_=run_case()
    assert r['publishable'] is False
    assert 'POLICY_RETRIEVAL_MISSING' in r['reason_codes']


def test_conflicting_recorded_histories_block_before_publication():
    count=[0]
    def contradictory(account_id):
        count[0]+=1
        rows=copy.deepcopy(ROWS)
        if count[0]>1: rows[-1]['score']=500
        return rows
    with patch.dict(agent.ALL_TOOL_DISPATCH, {'get_history':contradictory}):
        r,_=run_case(extra_history=True)
    assert r['publishable'] is False
    assert 'UNTRUSTED' not in r['answer']


def test_zero_raw_events_cannot_be_published_as_event_narrative():
    quiet_rows=[dict(row,score=600+i) for i,row in enumerate(ROWS)]
    with patch.dict(agent.ALL_TOOL_DISPATCH, {'get_history':lambda account_id:quiet_rows}):
        r,_=run_case(payload(count=0))
    assert r['publishable'] is False
    assert 'COMPOSITION_UNSUPPORTED' in r['reason_codes']


def test_bad_market_or_missing_context_rejected_before_llm():
    with pytest.raises(ValueError): run_case(market=3)
    with pytest.raises(ValueError): run_case(market=None)
    with pytest.raises(ValueError): run_case(account=None)


def test_unknown_scope_rejected_before_llm():
    with pytest.raises(ValueError): run_case(scope='made_up')


def test_temporal_rendering_error_returns_generic_fallback_not_draft():
    with patch.object(agent,'compose_verified_decline_event_narrative',side_effect=agent.TemporalNarrativeError('no valid event')):
        r,_=run_case()
    assert r['publishable'] is False
    assert 'COMPOSITION_UNSUPPORTED' in r['reason_codes']
    assert 'UNTRUSTED' not in r['answer']


def test_verification_exception_returns_generic_fallback_not_draft():
    with patch.object(agent,'verify_structured_response',side_effect=RuntimeError('bad verification')):
        r,_=run_case()
    assert r['publishable'] is False
    assert 'VERIFICATION_ERROR' in r['reason_codes']
    assert 'UNTRUSTED' not in r['answer']


def test_existing_numeric_claim_scope_is_unchanged():
    d=payload();d['policy_claims']=[]
    d['score_claims']=[{'account_id':ACCOUNT,'period':AS_OF,'metric':'score','value':271,'market':MARKET},
                       {'account_id':ACCOUNT,'period':AS_OF,'metric':'bin','value':6,'market':MARKET}]
    r,_=run_case(d,scope='score_bin')
    assert r['publishable'] is True,r
    assert 'the verified score was 271' in r['answer']
    assert 'POL-002 evaluation window' not in r['answer']
