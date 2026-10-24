"""Module 5.5.6.3: temporal policy attribution, without LLM or DB calls."""
from copy import deepcopy

import pytest

from src.temporal_narrative import (
    TemporalNarrativeError,
    AttributedDeclineEvent,
    attribute_verified_decline_events,
    compose_verified_decline_event_narrative,
)

ACCOUNT, AS_OF, MARKET = 81000001, "2025-04-01", 1
DEC, JAN, FEB, MAR = (
    "2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01"
)
PERIODS = [DEC, JAN, FEB, MAR]


def history(april=271):
    return [
        {"period": period, "score": score, "bin": 6, "market": MARKET}
        for period, score in zip(PERIODS + [AS_OF], [286, 282, 275, 267, april])
    ]


def tools(april=271):
    return [{"tool_name": "get_history", "tool_call_id": "h1",
             "arguments": {"account_id": ACCOUNT}, "result": history(april)}]


def event(period=MAR, evidence=None, review_state="eligible"):
    return {"criterion": "consecutive_declines", "period": period,
            "evidence_periods": list(PERIODS if evidence is None else evidence),
            "review_state": review_state}


def audit(count=1, events=None, flags=None):
    claim = {"field": "raw_event_count.consecutive_declines", "value": count,
             "account_id": ACCOUNT, "as_of_period": AS_OF, "market": MARKET}
    return {
        "status": "passed", "schema": {"status": "passed"},
        "issues": [], "limitations": [],
        "answer": "Untrusted LLM response: register updated and automatic default!",
        "narrative": {"coverage_method": "llm_draft_excluded", "status": "not_applicable"},
        "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "passed", "checks": [{
            "status": "passed", "claim": claim,
            "audit": {
                "status": "passed", "account_id": ACCOUNT,
                "as_of_period": AS_OF, "market": MARKET,
                "unchecked_claims": [],
                "checks": [{"field": claim["field"], "claimed": count,
                            "expected": count, "context_matches": True,
                            "value_matches": True, "passed": True}],
                "evidence": {"window_start": FEB, "window_end": AS_OF,
                             "events": deepcopy([event()] if events is None else events),
                             "data_quality_flags": deepcopy([] if flags is None else flags),
                             "policy_sources": ["POL-002", "POL-004", "POL-005", "POL-007"]},
            },
        }]},
    }


def flag():
    return {"kind": "exact_reversal_candidate", "period": MAR,
            "affected_periods": [JAN, FEB], "policy": "POL-005"}


def attrib(a=None, t=None, **kw):
    return attribute_verified_decline_events(
        audit() if a is None else a,
        tools() if t is None else t,
        account_id=kw.get("account_id", ACCOUNT),
        as_of_period=kw.get("as_of_period", AS_OF),
        market=kw.get("market", MARKET))


def compose(a=None, t=None, **kw):
    return compose_verified_decline_event_narrative(
        audit() if a is None else a,
        tools() if t is None else t,
        account_id=kw.get("account_id", ACCOUNT),
        as_of_period=kw.get("as_of_period", AS_OF),
        market=kw.get("market", MARKET))


def inner(a):
    return a["policy"]["checks"][0]["audit"]["evidence"]


def test_attribution_links_exact_dates_and_state():
    (e,) = attrib()
    assert isinstance(e, AttributedDeclineEvent)
    assert (e.event_period, e.evidence_periods, e.review_state) == (
        MAR, tuple(PERIODS), "eligible")


def test_eligible_narrative_contains_only_supported_meaning():
    out = compose()
    assert "POL-002" in out
    assert f"from {FEB} to {AS_OF}" in out
    assert f"{DEC} through {MAR}" in out
    assert "eligible for early-warning consideration" in out
    assert "not establish whether an operational warning" in out
    assert "automatic default" not in out


def test_model_draft_never_changes_public_narrative():
    a = audit()
    baseline = compose(a)
    a["answer"] = "500% chance of default, registered active warning"
    assert compose(a) == baseline


def test_pending_data_quality_is_not_reported_as_eligible():
    a = audit(events=[event(review_state="pending_data_quality")], flags=[flag()])
    result = compose(a)
    assert "pending data-quality review under POL-005" in result
    assert "it is not yet eligible" in result
    assert "as eligible for early-warning consideration" not in result


def test_pending_without_matching_data_quality_evidence_rejected():
    a = audit(events=[event(review_state="pending_data_quality")])
    with pytest.raises(TemporalNarrativeError, match="review state conflicts"):
        compose(a)


def test_eligible_with_overlapping_data_quality_flag_rejected():
    with pytest.raises(TemporalNarrativeError, match="review state conflicts"):
        compose(audit(flags=[flag()]))


def test_pending_without_pol005_source_rejected():
    a = audit(events=[event(review_state="pending_data_quality")], flags=[flag()])
    inner(a)["policy_sources"].remove("POL-005")
    with pytest.raises(TemporalNarrativeError, match="POL-005 attribution"):
        compose(a)


def test_missing_pol002_source_rejected():
    a = audit()
    inner(a)["policy_sources"].remove("POL-002")
    with pytest.raises(TemporalNarrativeError, match="POL-002 source"):
        compose(a)


def test_missing_event_review_state_rejected():
    a = audit()
    del inner(a)["events"][0]["review_state"]
    with pytest.raises(TemporalNarrativeError, match="review state"):
        compose(a)


def test_unknown_review_state_rejected():
    a = audit(events=[event(review_state="cleared")])
    with pytest.raises(TemporalNarrativeError, match="review state"):
        compose(a)


def test_wrong_event_date_rejected_even_when_count_matches():
    a = audit(events=[event(period=AS_OF)])
    with pytest.raises(TemporalNarrativeError, match="dated supporting evidence"):
        compose(a)


def test_wrong_evidence_period_rejected_even_when_count_matches():
    a = audit(events=[event(evidence=["2024-11-01", JAN, FEB, MAR])])
    with pytest.raises(TemporalNarrativeError, match="identity disagrees"):
        compose(a)


def test_duplicate_policy_event_rejected():
    a = audit(count=2, events=[event(), event()])
    # Two matching raw-policy events, but only one observed event: reject.
    with pytest.raises(TemporalNarrativeError, match="count or evidence"):
        compose(a)


def test_multiple_distinct_events_rendered_without_omission():
    a = audit(count=2, events=[event(), event(AS_OF, [JAN, FEB, MAR, AS_OF])])
    out = compose(a, tools(april=260))
    assert out.count("Three consecutive monthly score declines were observed") == 2
    assert f"through {MAR}" in out and f"through {AS_OF}" in out


def test_one_of_multiple_events_withheld_on_data_quality():
    a = audit(count=2, events=[event(), event(AS_OF, [JAN, FEB, MAR, AS_OF],
                                             review_state="pending_data_quality")],
              flags=[{"kind": "spike", "period": AS_OF,
                      "affected_periods": [AS_OF], "policy": "POL-005"}])
    out = compose(a, tools(april=260))
    assert "eligible for early-warning consideration" in out
    assert "pending data-quality review under POL-005" in out


def test_zero_event_blocks_because_this_is_event_narrative():
    with pytest.raises(TemporalNarrativeError, match="count or evidence"):
        compose(audit(count=0, events=[]))


def test_nonstandard_market_not_silently_treated_as_three_declines():
    with pytest.raises(TemporalNarrativeError, match="market 1"):
        compose(market=3)


def test_bad_caller_context_rejected():
    with pytest.raises(TemporalNarrativeError):
        compose(account_id=123)
    with pytest.raises(TemporalNarrativeError):
        compose(as_of_period="2025-05-01")


def test_bad_policy_verdict_blocks():
    a = audit()
    a["status"] = "failed"
    with pytest.raises(TemporalNarrativeError):
        compose(a)


def test_more_than_one_claim_blocks():
    a = audit()
    a["policy"]["checks"].append(deepcopy(a["policy"]["checks"][0]))
    with pytest.raises(TemporalNarrativeError):
        compose(a)


def test_malformed_quality_flag_blocks():
    with pytest.raises(TemporalNarrativeError, match="Malformed data-quality"):
        compose(audit(flags=[{"kind": "spike"}]))


def test_unrelated_policy_event_does_not_change_decline_attribution():
    a = audit(events=[event(), {"criterion": "score_drop", "period": MAR,
                                "evidence_periods": [FEB, MAR], "review_state": "eligible"}])
    out = compose(a)
    assert out.count("Three consecutive monthly score declines") == 1
    assert "do not establish whether" in out


def test_conflicting_tool_history_rejected():
    t = tools() + tools(april=999)
    with pytest.raises(TemporalNarrativeError):
        compose(t=t)


def test_no_tool_evidence_rejected():
    with pytest.raises(TemporalNarrativeError):
        compose(t=[])


def test_numerical_claims_cannot_be_silently_omitted():
    a = audit()
    a["numeric"] = {"status": "passed", "checks": [{
        "status": "passed", "value_grounded": True, "context_grounded": True,
        "claim": {"account_id": ACCOUNT, "period": AS_OF, "market": MARKET,
                  "metric": "score", "value": 271},
    }]}
    with pytest.raises(TemporalNarrativeError, match="Numerical claims"):
        compose(a)
