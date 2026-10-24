"""Module 5.5.6.1: deterministic evidence-only tests (no DB, LLM, or network)."""

from copy import deepcopy

import pytest

from src.temporal_evidence import (
    ObservedTemporalEvent, TemporalEvidenceError, detect_three_consecutive_declines,
)

ACCOUNT = 81000001
MARKET = 1


def make_rows():
    periods_scores = [
        ("2024-12-01", 286),
        ("2025-01-01", 282),
        ("2025-02-01", 275),
        ("2025-03-01", 267),
        ("2025-04-01", 271),
    ]
    return [{"period": p, "score": s, "bin": 6, "market": MARKET}
            for p, s in periods_scores]


def evidence(rows=None, *, account=ACCOUNT):
    return [{"tool_call_id": "h1", "tool_name": "get_history",
             "arguments": {"account_id": account},
             "result": deepcopy(make_rows() if rows is None else rows)}]


def detect(ev=None, *, as_of="2025-04-01", market=MARKET, account=ACCOUNT):
    return detect_three_consecutive_declines(
        evidence() if ev is None else ev,
        account_id=account, market=market, as_of_period=as_of,
    )


def test_three_monthly_declines_end_in_march():
    (event,) = detect()
    assert isinstance(event, ObservedTemporalEvent)
    assert event.kind == "three_consecutive_declines"
    assert event.event_period == "2025-03-01"
    assert event.as_of_period == "2025-04-01"
    assert event.scores == (286, 282, 275, 267)
    assert event.score_changes == (-4, -7, -8)
    assert event.evidence_periods == (
        "2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01"
    )


def test_march_reporting_cuts_off_april():
    (event,) = detect(as_of="2025-03-01")
    assert event.as_of_period == "2025-03-01"
    assert event.event_period == "2025-03-01"


def test_april_recovery_is_not_an_ongoing_streak():
    events = detect()
    assert not any(e.event_period == "2025-04-01" for e in events)
    assert any(e.event_period == "2025-03-01" for e in events)


def test_future_changes_cannot_affect_march_results():
    before = detect(as_of="2025-03-01")
    rows = make_rows()
    rows[-1]["score"] = 100
    after = detect(evidence(rows), as_of="2025-03-01")
    assert before == after


def test_gap_in_calendar_months_does_not_create_a_streak():
    rows = make_rows()
    rows[2]["period"] = "2025-02-01"
    rows[1]["period"] = "2024-11-01"  # no January observation
    assert detect(evidence(rows)) == ()


def test_equal_score_interrupts_declines():
    rows = make_rows()
    rows[2]["score"] = rows[1]["score"]
    assert detect(evidence(rows)) == ()


def test_missing_history_is_not_interpreted_as_no_event():
    with pytest.raises(TemporalEvidenceError, match="missing"):
        detect([])


def test_missing_requested_period_is_rejected():
    with pytest.raises(TemporalEvidenceError, match="Reporting period"):
        detect(as_of="2025-05-01")


def test_insufficient_observations_rejected():
    with pytest.raises(TemporalEvidenceError, match="Fewer than four"):
        detect(evidence(make_rows()[:3]), as_of="2025-02-01")


def test_mismatched_market_rejected():
    with pytest.raises(TemporalEvidenceError, match="inconsistent"):
        detect(market=2)


def test_noncanonical_date_rejected():
    with pytest.raises(TemporalEvidenceError, match="ISO|YYYY"):
        detect(as_of="01.04.2025")


def test_boolean_ids_rejected():
    with pytest.raises(TemporalEvidenceError, match="integer"):
        detect(account=True)


def test_duplicate_month_rejected():
    rows = make_rows()
    rows.append(deepcopy(rows[0]))
    with pytest.raises(TemporalEvidenceError, match="Multiple observations"):
        detect(evidence(rows))


def test_identical_duplicate_history_calls_are_accepted():
    calls = evidence()
    assert detect(calls + deepcopy(calls)) == detect(calls)


def test_conflicting_duplicate_history_calls_are_rejected():
    calls = evidence()
    other = deepcopy(calls[0])
    other["result"][0]["score"] = 500
    with pytest.raises(TemporalEvidenceError, match="Conflicting"):
        detect(calls + [other])


def test_unrelated_tool_calls_do_not_change_result():
    other = {"tool_name": "retrieve_policy", "arguments": {"query": "abc"}, "result": []}
    assert detect(evidence() + [other]) == detect()


def test_longer_decline_streak_emits_each_completed_three_decline_window():
    rows = make_rows()
    rows[-1]["score"] = 260
    events = detect(evidence(rows))
    assert tuple(e.event_period for e in events) == ("2025-03-01", "2025-04-01")


def test_events_do_not_assert_policy_or_operational_warning():
    (event,) = detect()
    assert "policy_ids" not in event.__dataclass_fields__
    assert "warning_status" not in event.__dataclass_fields__
