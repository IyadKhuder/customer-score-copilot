"""Module 5.3.3: independently verified policy claims (no LLM required)."""

from datetime import date

import pytest

from src.policy_claim_guard import audit_labeled_policy_answer, audit_policy_claims


def history(scores, bins=None, *, market=1, start_year=2024, start_month=7):
    bins = bins if bins is not None else [6] * len(scores)
    index = start_year * 12 + start_month - 1
    return [
        {"period": date((index + i) // 12, (index + i) % 12 + 1, 1).isoformat(),
         "score": score, "bin": bin_, "market": market}
        for i, (score, bin_) in enumerate(zip(scores, bins))
    ]


def claim(field, value, *, account_id=81000001, period="2025-02-01", market=1):
    return {"field": field, "value": value, "account_id": account_id,
            "as_of_period": period, "market": market}


def audit(claims, scores=None, bins=None, **kwargs):
    scores = [600] * 7 + [560] if scores is None else scores
    return audit_policy_claims(claims, history(scores, bins),
                               account_id=81000001, as_of_period="2025-02-01", **kwargs)


def test_correct_warning_claim_passes():
    result = audit([claim("warning_signal_status", "triggered")])
    assert result["status"] == "passed"
    assert result["checks"][0]["passed"]


def test_wrong_no_warning_claim_fails_even_with_genuine_scores():
    result = audit([claim("warning_signal_status", "not_triggered")])
    assert result["status"] == "failed"
    assert result["checks"][0]["expected"] == "triggered"


def test_wrong_month_fails_context_even_if_status_correct():
    result = audit([claim("warning_signal_status", "triggered", period="2024-02-01")])
    assert result["status"] == "failed"
    assert result["checks"][0]["context_matches"] is False


def test_wrong_account_fails_context_even_if_status_correct():
    result = audit([claim("warning_signal_status", "triggered", account_id=999999)])
    assert result["status"] == "failed"


def test_wrong_market_fails_context_even_if_status_correct():
    result = audit([claim("warning_signal_status", "triggered", market=3)])
    assert result["status"] == "failed"


def test_unknown_field_is_incomplete_not_passed():
    result = audit([claim("formal_case_status", "closed")])
    assert result["status"] == "incomplete"
    assert result["unchecked_claims"][0]["reason"] == "unsupported_field"


def test_no_claims_cannot_pass():
    assert audit([])["status"] == "incomplete"


def test_multiple_claims_fail_when_any_one_is_wrong():
    claims = [claim("warning_signal_status", "triggered"),
              claim("analyst_referral", "indicated")]
    result = audit(claims)
    assert result["status"] == "failed"  # one score-drop criterion, no referral


def test_rolling_window_event_count_is_recomputed():
    result = audit([claim("raw_event_count.score_drop", 1)])
    assert result["status"] == "passed"
    result = audit([claim("raw_event_count.score_drop", 2)])
    assert result["status"] == "failed"


def test_event_count_rejects_bool_as_numeric_count():
    result = audit([claim("raw_event_count.score_drop", True)])
    assert result["status"] == "failed"


def test_policy_thresholds_are_independently_checked():
    result = audit([claim("policy_applicability.thresholds.score_drop", 40)])
    assert result["status"] == "passed"
    result = audit([claim("policy_applicability.thresholds.score_drop", 50)])
    assert result["status"] == "failed"


def test_old_score_drop_expires_from_window():
    scores = [600, 550, 550, 550, 550, 550, 550, 550]
    result = audit([claim("raw_event_count.score_drop", 0)], scores=scores)
    assert result["status"] == "passed"


def test_data_quality_hold_is_not_a_cleared_warning():
    result = audit(
        [claim("warning_signal_status", "not_triggered"),
         claim("risk_action_gate", "requires_human_authorization")],
        scores=[600]*7 + [420], bins=[6]*7 + [3],
    )
    assert result["status"] == "failed"
    assert result["checks"][0]["expected"] == "pending_data_quality"
    assert result["checks"][1]["expected"] == "hold_pending_data_quality"


def test_site_wide_committee_rule_cannot_be_falsely_cleared():
    result = audit([claim("committee_site_aggregate_rule", "not_indicated")])
    assert result["status"] == "failed"
    assert result["checks"][0]["expected"] == "not_evaluated"


def test_grace_threshold_is_checked_from_explicit_onboarding():
    rows = history([600]*7 + [551], market=3)
    correct = claim("policy_applicability.thresholds.score_drop", 50, market=3)
    result = audit_policy_claims([correct], rows, account_id=81000001,
                                 as_of_period="2025-02-01",
                                 site_onboarded_period="2024-05-01")
    assert result["status"] == "passed"
    wrong = claim("policy_applicability.thresholds.score_drop", 40, market=3)
    assert audit_policy_claims([wrong], rows, account_id=81000001,
                                as_of_period="2025-02-01",
                                site_onboarded_period="2024-05-01")["status"] == "failed"


def test_three_bin_committee_rule_not_waived_in_grace():
    rows = history([600]*7 + [595], [6]*7 + [3], market=3)
    assertions = [claim("committee_review_account_rules", "not_indicated", market=3)]
    result = audit_policy_claims(assertions, rows, account_id=81000001,
                                 as_of_period="2025-02-01",
                                 site_onboarded_period="2024-05-01")
    assert result["status"] == "failed"
    assert result["checks"][0]["expected"] == "indicated"


def test_too_short_history_cannot_confirm_no_warning():
    short = history([600]*6, start_month=9)
    result = audit_policy_claims(
        [claim("warning_signal_status", "not_triggered")],
        short, account_id=81000001, as_of_period="2025-02-01",
    )
    assert result["status"] == "failed"
    assert result["checks"][0]["expected"] == "insufficient_evidence"


def test_labeled_text_in_correct_format_is_validated():
    answer = "Warning signal: triggered\nAnalyst referral: not_indicated"
    result = audit_labeled_policy_answer(answer, history([600]*7+[560]),
                                         account_id=81000001, as_of_period="2025-02-01")
    assert result["status"] == "passed"
    assert len(result["checks"]) == 2


def test_labeled_text_with_fabricated_status_fails():
    result = audit_labeled_policy_answer(
        "Warning signal: not_triggered", history([600]*7+[560]),
        account_id=81000001, as_of_period="2025-02-01")
    assert result["status"] == "failed"


def test_unrecognized_prose_is_not_passed():
    result = audit_labeled_policy_answer(
        "No warning is active.", history([600]*7+[560]),
        account_id=81000001, as_of_period="2025-02-01")
    assert result["status"] == "incomplete"
    assert result["unchecked_text"] == ["No warning is active."]


def test_mixed_formatted_and_unrecognized_prose_is_incomplete():
    result = audit_labeled_policy_answer(
        "Warning signal: triggered\nThis is a low-risk customer.",
        history([600]*7+[560]), account_id=81000001,
        as_of_period="2025-02-01")
    assert result["status"] == "incomplete"
    assert len(result["checks"]) == 1


def test_account_metadata_in_history_must_match():
    rows = history([600]*8)
    for row in rows:
        row["account_id"] = 123
    with pytest.raises(ValueError, match="History account_id"):
        audit_policy_claims([claim("warning_signal_status", "not_triggered")], rows,
                            account_id=81000001, as_of_period="2025-02-01")


def test_malformed_claim_is_incomplete():
    result = audit([{"field": "warning_signal_status", "value": "triggered"}])
    assert result["status"] == "incomplete"
