"""Module 5.5.3: isolated policy-composer tests (no provider/DB access)."""
from copy import deepcopy

import pytest

from src.narrative_composer import (
    CompositionError,
    compose_verified_warning_signal,
    compose_verified_score_bin,
)


CONTEXT = {"account_id": 81000001, "as_of_period": "2025-03-01", "market": 1}


def passed_policy_audit():
    claim = {
        "field": "warning_signal_status", "value": "triggered",
        **CONTEXT,
    }
    return {
        "status": "passed",
        "schema": {"status": "passed"},
        "issues": [], "limitations": [],
        "answer": "The LLM said a warning was recorded, but this text is untrusted.",
        "narrative": {"coverage_method": "llm_draft_excluded"},
        "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "passed", "checks": [{
            "status": "passed", "claim": claim,
            "audit": {"status": "passed", "checks": [{
                "field": "warning_signal_status",
                "claimed": "triggered", "expected": "triggered",
                "context_matches": True, "value_matches": True, "passed": True,
            }]},
        }]},
    }


def render(audit, **context):
    return compose_verified_warning_signal(audit, **(context or CONTEXT))


def test_renders_only_supported_verified_signal():
    output = render(passed_policy_audit())
    assert output == (
        "As of 2025-03-01, the verified early-warning signal "
        "for account 81000001 in market 1 was triggered. "
        "This is a policy-evaluated signal, not confirmation that "
        "a warning was entered in an operational register."
    )


def test_ignores_untrusted_model_answer():
    audit = passed_policy_audit()
    baseline = render(audit)
    audit["answer"] = "The account is guaranteed to default and was flagged."
    assert render(audit) == baseline
    assert "guaranteed to default" not in baseline


def test_rejects_mismatched_caller_context():
    with pytest.raises(CompositionError):
        render(passed_policy_audit(), account_id=81000999,
               as_of_period="2025-03-01", market=1)


def test_rejects_wrong_report_date():
    with pytest.raises(CompositionError):
        render(passed_policy_audit(), account_id=81000001,
               as_of_period="2025-03-02", market=1)


def test_rejects_failed_audit():
    audit = passed_policy_audit()
    audit["status"] = "failed"
    with pytest.raises(CompositionError):
        render(audit)


def test_rejects_incomplete_policy_evidence():
    audit = passed_policy_audit()
    audit["policy"]["checks"][0]["audit"]["checks"][0]["context_matches"] = False
    with pytest.raises(CompositionError):
        render(audit)


def test_rejects_unsupported_value_even_if_claim_matches_expected():
    audit = passed_policy_audit()
    claim = audit["policy"]["checks"][0]["claim"]
    check = audit["policy"]["checks"][0]["audit"]["checks"][0]
    claim["value"] = "not_triggered"
    check["claimed"] = "not_triggered"
    check["expected"] = "not_triggered"
    with pytest.raises(CompositionError):
        render(audit)


def test_rejects_unsupported_policy_field():
    audit = passed_policy_audit()
    audit["policy"]["checks"][0]["claim"]["field"] = "recent_substandard_entry"
    with pytest.raises(CompositionError):
        render(audit)


def test_rejects_multiple_policy_claims_even_when_each_passed():
    audit = passed_policy_audit()
    audit["policy"]["checks"].append(deepcopy(audit["policy"]["checks"][0]))
    with pytest.raises(CompositionError):
        render(audit)


def test_rejects_numeric_claims_instead_of_omitting_them():
    audit = passed_policy_audit()
    audit["numeric"] = {"status": "passed", "checks": [
        {"status": "passed", "value_grounded": True, "context_grounded": True}
    ]}
    with pytest.raises(CompositionError):
        render(audit)


def test_rejects_unresolved_limitations():
    audit = passed_policy_audit()
    audit["limitations"] = ["Missing operational warning register"]
    with pytest.raises(CompositionError):
        render(audit)


def test_rejects_legacy_narrative_mode():
    audit = passed_policy_audit()
    audit["narrative"]["coverage_method"] = "legacy_scan"
    with pytest.raises(CompositionError):
        render(audit)


def test_existing_numeric_composer_still_rejects_policy_claims():
    with pytest.raises(CompositionError, match="Policy claims are outside"):
        compose_verified_score_bin(passed_policy_audit(),
                                   account_id=81000001, period="2025-03-01")


def test_existing_numeric_composer_still_renders_clean_score_bin():
    claim_base = {"account_id": 81000001, "period": "2025-03-01", "market": 1}
    checks = [
        {"status": "passed", "context_grounded": True, "value_grounded": True,
         "claim": {**claim_base, "metric": "score", "value": 267}},
        {"status": "passed", "context_grounded": True, "value_grounded": True,
         "claim": {**claim_base, "metric": "bin", "value": 6}},
    ]
    audit = {"schema": {"status": "passed"}, "status": "passed", "issues": [],
             "limitations": [], "policy": {"status": "not_applicable", "checks": []},
             "numeric": {"status": "passed", "checks": checks}}
    assert compose_verified_score_bin(
        audit, account_id=81000001, period="2025-03-01"
    ) == "For account 81000001 on 2025-03-01, the verified score was 267 and the risk bin was 6 in market 1."
