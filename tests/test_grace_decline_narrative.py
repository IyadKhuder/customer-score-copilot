"""Module 5.5.7.3: threshold-aware POL-004 event narrative and gate tests."""

import copy
import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from src import agent
from src.grace_decline_narrative import (
    GraceDeclineNarrativeError, compose_verified_grace_declines,
)
from src.temporal_evidence import (
    TemporalEvidenceError, detect_consecutive_declines,
    detect_three_consecutive_declines,
)
from src.verification_pipeline import verify_structured_response

ACCOUNT, MARKET, AS_OF = 83000005, 3, "2025-04-01"
ONBOARDED, REVIEWED = "2024-05-01", "2025-04-01"
PERIODS = ["2024-09-01", "2024-10-01", "2024-11-01", "2024-12-01",
           "2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01"]
SCORES = [700, 690, 680, 670, 660, 650, 655, 662]
HELD_SCORES = [830, 660, 650, 640, 630, 620, 625, 632]
ROWS = [dict(period=p, score=s, bin=6, market=MARKET) for p, s in zip(PERIODS, SCORES)]
POL002 = dict(source="POL-002_early_warning_thresholds.md", text="Consecutive declines rule")
POL004 = dict(source="POL-004_site_market_exception_rules.md", text="New-market grace threshold")
POL005 = dict(source="POL-005_data_quality_and_volatility_flags.md", text="Data quality holds")
FIELDS = [
    ("policy_applicability.mode", "grace"),
    ("policy_applicability.thresholds.consecutive_declines", 4),
    ("policy_applicability.quarterly_exception_review", "current"),
    ("raw_event_count.consecutive_declines", 1),
]


def tools(rows=ROWS, docs=(POL002, POL004)):
    return [
        dict(tool_name="get_history", arguments={"account_id": ACCOUNT}, result=copy.deepcopy(rows)),
        dict(tool_name="retrieve_policy", arguments={"query": "POL-004 POL-002"}, result=copy.deepcopy(list(docs))),
    ]


def payload(fields=FIELDS):
    return dict(answer="UNTRUSTED: The warning is cleared; automatically approve credit!",
                score_claims=[], policy_claims=[
                    dict(field=k, value=v, account_id=ACCOUNT, as_of_period=AS_OF, market=MARKET)
                    for k,v in fields], limitations=[])


@pytest.fixture(autouse=True)
def validator_dependencies():
    # Unlike policy evaluators, the numerical guardrail is intentionally unused
    # by this event-only scope, but the verifier imports its symbols.
    guard = types.ModuleType("src.guardrail")
    guard.ScoreClaim = lambda **kw: kw
    guard.check_score_claim = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unexpected numeric check"))
    guard.extract_score_claims = lambda *a: (_ for _ in ()).throw(AssertionError("legacy extractor used"))
    guard.audit_answer = lambda *a: (_ for _ in ()).throw(AssertionError("legacy audit used"))
    with patch.dict(sys.modules, {"src.guardrail": guard}):
        yield


def audit(rows=ROWS, fields=FIELDS, onboarded=ONBOARDED, reviewed=REVIEWED):
    return verify_structured_response(
        json.dumps(payload(fields)), tools(rows), narrative_mode="claims_first",
        site_onboarded_period=onboarded,
        last_exception_review_period=reviewed,
    )


def render(a=None, t=None, **context):
    return compose_verified_grace_declines(
        audit() if a is None else a, tools() if t is None else t,
        **(context or dict(account_id=ACCOUNT, as_of_period=AS_OF, market=MARKET)),
    )


def test_four_declines_in_grace_window_publish_narrative():
    a = audit()
    assert a["status"] == "passed", a
    text = render(a)
    assert "four consecutive monthly declines" in text.lower()
    assert "2024-10-01 through 2025-02-01" in text
    assert "POL-004" in text and "POL-002" in text
    assert "eligible for early-warning consideration" in text
    assert "automatically approve" not in text
    assert "registered or cleared" in text


def test_held_decline_event_is_not_called_eligible():
    rows = [dict(period=p, score=s, bin=6, market=MARKET)
            for p,s in zip(PERIODS, HELD_SCORES)]
    a = audit(rows)
    assert a["status"] == "passed"
    assert a["policy"]["checks"][0]["audit"]["evidence"]["data_quality_flags"]
    text = render(a, tools(rows))
    assert "pending data-quality review under POL-005" in text
    assert "not yet eligible for escalation" in text


def test_policy_evaluator_verifies_review_mode_threshold_and_count():
    a = audit()
    details = {x["claim"]["field"]: x for x in a["policy"]["checks"]}
    assert all(x["status"] == "passed" for x in details.values())
    assert details["policy_applicability.thresholds.consecutive_declines"]["audit"]["checks"][0]["expected"] == 4
    assert details["policy_applicability.quarterly_exception_review"]["audit"]["checks"][0]["expected"] == "current"
    assert details["raw_event_count.consecutive_declines"]["audit"]["checks"][0]["expected"] == 1


def test_detector_uses_five_observations_and_maintains_three_wrapper():
    events = detect_consecutive_declines(tools(), account_id=ACCOUNT, as_of_period=AS_OF, market=MARKET, transitions=4)
    assert len(events) >= 2
    assert all(len(e.evidence_periods) == 5 and len(e.score_changes) == 4 for e in events)
    old = detect_three_consecutive_declines(tools(), account_id=ACCOUNT, as_of_period=AS_OF, market=MARKET)
    assert old and all(e.kind == "three_consecutive_declines" for e in old)


@pytest.mark.parametrize("transitions", [0, -1, True, "4", 13])
def test_detector_rejects_invalid_threshold(transitions):
    with pytest.raises(TemporalEvidenceError):
        detect_consecutive_declines(tools(), account_id=ACCOUNT, as_of_period=AS_OF,
                                    market=MARKET, transitions=transitions)


@pytest.mark.parametrize("field,value", [
    ("policy_applicability.mode", "standard"),
    ("policy_applicability.thresholds.consecutive_declines", 3),
    ("policy_applicability.quarterly_exception_review", "review_due"),
    ("raw_event_count.consecutive_declines", 2),
])
def test_false_policy_claims_fail_verification(field, value):
    modified = [(key, value if key == field else v) for key,v in FIELDS]
    a = audit(fields=modified)
    assert a["status"] == "failed"
    with pytest.raises(GraceDeclineNarrativeError):
        render(a)


@pytest.mark.parametrize("change", [
    lambda a: a.update(status="failed"),
    lambda a: a["issues"].append("an issue"),
    lambda a: a["limitations"].append("unresolved"),
    lambda a: a["narrative"].update(coverage_method="legacy_scan"),
    lambda a: a["policy"]["checks"].pop(),
    lambda a: a["policy"]["checks"].append(copy.deepcopy(a["policy"]["checks"][0])),
    lambda a: a["policy"]["checks"][0]["claim"].update(market=2),
    lambda a: a["policy"]["checks"][0]["audit"]["checks"][0].update(value_matches=False),
    lambda a: a["policy"]["checks"][0]["audit"]["evidence"].update(window_start="2025-01-01"),
    lambda a: a["policy"]["checks"][0]["audit"]["evidence"].update(policy_sources=["POL-002"]),
    lambda a: a["policy"]["checks"][0]["audit"]["evidence"]["events"][0].update(period="2025-03-01"),
    lambda a: a["policy"]["checks"][0]["audit"]["evidence"]["events"][0].update(review_state="pending_data_quality"),
])
def test_renderer_rejects_invalid_or_divergent_audits(change):
    a = audit()
    change(a)
    with pytest.raises(GraceDeclineNarrativeError):
        render(a)


def test_policy_checks_order_independent_and_prose_discarded():
    a = audit()
    text = render(a)
    a["policy"]["checks"].reverse()
    a["answer"] = "The score was 999999; automatic authorization granted"
    assert render(a) == text


def test_conflicting_recorded_history_rejected():
    t = tools()
    alternate = copy.deepcopy(t[0])
    alternate["result"][0]["score"] += 2
    t.append(alternate)
    with pytest.raises(GraceDeclineNarrativeError):
        render(t=t)


def test_non_grace_onboarding_and_overdue_review_are_not_publishable():
    old = audit(onboarded="2023-10-01")
    assert old["status"] == "failed"   # actual mode standard, not grace
    stale = audit(reviewed="2024-11-01")
    assert stale["status"] == "failed" # quarterly review due


def test_missing_five_point_baseline_blocks():
    with pytest.raises(GraceDeclineNarrativeError):
        render(t=tools(ROWS[-4:]))


def test_mismatched_reporting_context_blocks():
    for kw in (dict(account_id=ACCOUNT + 1, as_of_period=AS_OF, market=MARKET),
               dict(account_id=ACCOUNT, as_of_period="2025-03-01", market=MARKET),
               dict(account_id=ACCOUNT, as_of_period=AS_OF, market=1)):
        with pytest.raises(GraceDeclineNarrativeError):
            render(**kw)


@dataclass
class Call:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class Scripted:
    def __init__(self, *responses):
        self.responses = list(responses)

    def chat(self, messages, tools):
        self.messages = messages
        return self.responses.pop(0)

    def continue_with_tool_results(self, assistant_response, tool_results, tools):
        return self.responses.pop(0)


def run_agent_case(fields=FIELDS, rows=ROWS, docs=(POL002, POL004), *, onboarded=ONBOARDED,
                   reviewed=REVIEWED):
    scripted = Scripted(
        Reply(tool_calls=[Call("h", "get_history", {"account_id": ACCOUNT}),
                          Call("p", "retrieve_policy", {"query": "POL-002 POL-004"})]),
        Reply(text=json.dumps(payload(fields))),
    )
    with patch.dict(agent.ALL_TOOL_DISPATCH, {
        "get_history": lambda account_id: copy.deepcopy(rows),
        "retrieve_policy": lambda query: copy.deepcopy(list(docs)),
    }):
        result = agent.run_agent(
            "Explain grace-market declines", scripted, verbose=False,
            publication_mode="claims_first", claim_scope="grace_decline_events",
            expected_account_id=ACCOUNT, expected_period=AS_OF, expected_market=MARKET,
            site_onboarded_period=onboarded, last_exception_review_period=reviewed,
        )
    return result


def test_gate_publishes_only_verified_event_prose():
    result = run_agent_case()
    assert result["publishable"], result
    assert result["status"] == "passed"
    assert result["verification"]["rendering"]["method"] == "verified_grace_decline_events"
    assert "POL-004 grace-period exception" in result["answer"]
    assert "UNTRUSTED" not in result["answer"]


@pytest.mark.parametrize("docs", [(), (POL002,), (POL004,)])
def test_gate_requires_both_policy_memos(docs):
    result = run_agent_case(docs=docs)
    assert not result["publishable"]
    assert "POLICY_RETRIEVAL_MISSING" in result["reason_codes"]


def test_gate_requires_quality_memo_for_pending_event():
    held = [dict(period=p, score=s, bin=6, market=MARKET)
            for p,s in zip(PERIODS, HELD_SCORES)]
    without = run_agent_case(rows=held)
    assert not without["publishable"]
    assert "POLICY_RETRIEVAL_MISSING" in without["reason_codes"]
    with_memo = run_agent_case(rows=held, docs=(POL002, POL004, POL005))
    assert with_memo["publishable"], with_memo
    assert "pending data-quality review under POL-005" in with_memo["answer"]


def test_gate_rejects_stale_site_review():
    result = run_agent_case(reviewed="2024-10-01")
    assert not result["publishable"]
    assert "UNTRUSTED" not in result["answer"]


def test_gate_blocks_without_trusted_site_metadata():
    with pytest.raises(ValueError):
        agent.run_agent("x", Scripted(), verbose=False,
                        publication_mode="claims_first", claim_scope="grace_decline_events",
                        expected_account_id=ACCOUNT, expected_period=AS_OF, expected_market=MARKET)


def test_gate_rejects_market_one_grace_scope():
    with pytest.raises(ValueError):
        agent.run_agent("x", Scripted(), verbose=False,
                        publication_mode="claims_first", claim_scope="grace_decline_events",
                        expected_account_id=ACCOUNT, expected_period=AS_OF, expected_market=1,
                        site_onboarded_period=ONBOARDED, last_exception_review_period=REVIEWED)
