"""Module 5.3.2: isolated policy decisions, no LLM or database required."""

import csv
from datetime import date, datetime
from pathlib import Path

import pytest

from src.policy_decisions import evaluate_policy_decision


def history(scores, bins=None, *, market=1, start_year=2024, start_month=7):
    bins = [6] * len(scores) if bins is None else bins
    assert len(scores) == len(bins)
    start_index = start_year * 12 + start_month - 1
    return [
        {"period": date((start_index + i)//12,
                        (start_index + i)%12 + 1, 1).isoformat(),
         "score": score, "bin": bin_, "market": market}
        for i, (score, bin_) in enumerate(zip(scores, bins))
    ]


def check(rows, **kwargs):
    return evaluate_policy_decision(rows, rows[-1]["period"], **kwargs)


def test_site_one_uses_standard_thresholds_without_onboarding_metadata():
    result = check(history([600]*7 + [560]))
    assert result["policy_applicability"]["mode"] == "standard"
    assert result["policy_applicability"]["thresholds"]["score_drop"] == 40
    assert result["warning_signal_status"] == "triggered"
    assert result["analyst_referral"] == "not_indicated"
    assert result["committee_review_account_rules"] == "not_indicated"


def test_grace_25_percent_relaxation_and_integer_rounding():
    result = check(history([600]*7 + [551], market=3),
                   site_onboarded_period="2024-05-01")
    policy = result["policy_applicability"]
    assert policy["mode"] == "grace"
    assert policy["thresholds"] == {
        "score_drop": 50, "bin_drop": 3,
        "consecutive_declines": 4, "volatility": 43.75,
    }
    assert result["warning_signal_status"] == "not_triggered"  # 49 isn't 50
    assert result["manual_policy_review_required"] is True


def test_grace_50_point_drop_is_inclusive():
    result = check(history([600]*7 + [550], market=3),
                   site_onboarded_period="2024-05-01",
                   last_exception_review_period="2025-01-01")
    assert result["warning_signal_status"] == "triggered"
    assert any(e["criterion"] == "score_drop" and e["score_change"] == -50
               for e in result["events"])
    assert result["manual_policy_review_required"] is False


def test_grace_four_consecutive_declines_not_three():
    rows = history([600]*4 + [595, 590, 585, 585], market=3)
    result = check(rows, site_onboarded_period="2024-05-01")
    assert result["warning_signal_status"] == "not_triggered"
    assert not any(e["criterion"] == "consecutive_declines" for e in result["events"])
    rows = history([600]*3 + [599, 598, 597, 596, 596], market=3)
    result = check(rows, site_onboarded_period="2024-05-01")
    assert any(e["criterion"] == "consecutive_declines" for e in result["events"])


def test_grace_35_standard_volatility_is_relaxed_to_over_43_75():
    scores = [600]*6 + [690, 690]
    standard = check(history(scores))
    grace = check(history(scores, market=3), site_onboarded_period="2024-05-01")
    assert any(e["criterion"] == "volatility" for e in standard["events"])
    assert not any(e["criterion"] == "volatility" for e in grace["events"])


def test_grace_expires_after_twelve_elapsed_calendar_months():
    result = check(history([600]*7 + [560], market=2),
                   site_onboarded_period="2024-02-01")
    assert result["policy_applicability"]["mode"] == "standard"
    assert result["warning_signal_status"] == "triggered"


def test_unknown_market_onboarding_is_not_guessed_from_score_history():
    with pytest.raises(ValueError, match="site_onboarded_period"):
        check(history([600]*8, market=3))


def test_quarterly_review_status_is_explicit():
    rows = history([600]*8, market=3)
    current = check(rows, site_onboarded_period="2024-05-01",
                    last_exception_review_period="2025-01-01")
    due = check(rows, site_onboarded_period="2024-05-01",
                last_exception_review_period="2024-11-01")
    assert current["policy_applicability"]["quarterly_exception_review"] == "current"
    assert due["policy_applicability"]["quarterly_exception_review"] == "review_due"
    assert due["manual_policy_review_required"] is True


def test_analyst_triggered_by_distinct_criteria_across_two_months():
    # December: 40-point score loss, January: 2-bin downgrade.
    scores = [600]*5 + [560, 555, 555]
    bins = [6]*6 + [4, 4]
    result = check(history(scores, bins))
    assert result["analyst_referral"] == "indicated"
    assert result["committee_review_account_rules"] == "not_indicated"


def test_two_distinct_criteria_in_one_month_go_to_committee():
    result = check(history([600]*7 + [560], [6]*7 + [4]))
    assert result["analyst_referral"] == "indicated"
    assert result["committee_review_account_rules"] == "indicated"


def test_three_bin_drop_is_never_waived_by_grace_period():
    result = check(history([600]*7 + [595], [6]*7 + [3], market=3),
                   site_onboarded_period="2024-05-01")
    assert result["committee_review_account_rules"] == "indicated"
    assert len(result["severe_downward_migrations"]) == 1
    assert result["severe_downward_migrations"][0]["bin_change"] == -3


def test_two_bin_drop_alone_does_not_trigger_grace_period():
    result = check(history([600]*7 + [595], [6]*7 + [4], market=3),
                   site_onboarded_period="2024-05-01")
    assert result["warning_signal_status"] == "not_triggered"
    assert result["committee_review_account_rules"] == "not_indicated"


def test_over_150_goes_to_data_quality_before_risk_escalation():
    result = check(history([600]*7 + [420], [6]*7 + [3]))
    assert result["data_quality_review"] == "required"
    assert any(f["kind"] == "single_change_over_150" for f in result["data_quality_flags"])
    assert result["warning_signal_status"] == "pending_data_quality"
    assert result["analyst_referral"] == "pending_data_quality"
    assert result["committee_review_account_rules"] == "pending_data_quality"
    assert result["risk_action_gate"] == "hold_pending_data_quality"


def test_exact_reversal_flags_pending_event_in_recent_window():
    result = check(history([600]*5 + [550, 600, 600]))
    assert any(f["kind"] == "exact_reversal_candidate" for f in result["data_quality_flags"])
    assert any(e["review_state"] == "pending_data_quality" for e in result["events"])
    assert result["warning_signal_status"] == "pending_data_quality"


def test_exactly_150_is_not_probable_error_by_magnitude_alone():
    result = check(history([600]*7 + [450]))
    assert not any(f["kind"] == "single_change_over_150" for f in result["data_quality_flags"])
    assert result["warning_signal_status"] == "triggered"


def test_old_event_outside_rolling_three_months_not_counted():
    scores = [600, 560, 560, 560, 560, 555, 555, 555]
    bins = [6, 6, 6, 6, 6, 4, 4, 4]
    result = check(history(scores, bins))
    assert result["analyst_referral"] == "not_indicated"
    assert all(e["period"] >= "2024-12-01" for e in result["events"])


def test_missing_history_produces_indeterminate_negative_decision():
    rows = history([600]*6, start_month=9)
    result = check(rows)
    assert result["warning_signal_status"] == "insufficient_evidence"
    assert result["analyst_referral"] == "insufficient_evidence"
    assert result["committee_review_account_rules"] == "insufficient_evidence"


def test_mixed_market_history_is_rejected():
    rows = history([600]*8)
    rows[0]["market"] = 3
    with pytest.raises(ValueError, match="one consistent market"):
        check(rows)


def test_market_aggregate_committee_rule_is_not_falsely_cleared():
    result = check(history([600]*8))
    assert result["committee_site_aggregate_rule"] == "not_evaluated"
    assert result["administrative_warning_status"] == "not_determined"


def test_actual_market_3_account_has_data_quality_flags():
    source = Path(__file__).resolve().parents[1] / "data" / "scores_extended.csv"
    if not source.exists():
        pytest.fail("Required generated CSV missing; run python -m src.data_loader")
    with source.open(encoding="utf-8", newline="") as stream:
        records = [r for r in csv.DictReader(stream, delimiter=";")
                   if int(r["account_id"]) == 3000007]
    rows = [
        {"period": datetime.strptime(r["period"], "%d.%m.%Y").date().isoformat(),
         "score": int(r["score"]), "bin": int(r["bin"]), "market": int(r["market"])}
        for r in records
    ]
    result = evaluate_policy_decision(
        rows, "1991-12-01", site_onboarded_period="1991-01-01",
    )
    assert result["market"] == 3
    assert result["policy_applicability"]["mode"] == "grace"
    assert any(f["kind"] == "single_change_over_150" for f in result["data_quality_flags"])
    assert result["data_quality_review"] == "required"
