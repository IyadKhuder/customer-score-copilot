"""Module 5.3.3: audit explicit LLM policy claims against independently computed facts.

This checks *declared* claims, not arbitrary narrative text. No LLM calls.
We recompute policy decisions from trusted score history, not an LLM-provided
assessment. A clean audit means only that the supplied claims were correct.
"""

from __future__ import annotations

import re
from typing import Any

from src.policy_decisions import evaluate_policy_decision


# Field names are our small, deliberately closed claim schema.
_SIMPLE_FIELDS = {
    "warning_signal_status": lambda d: d["warning_signal_status"],
    "analyst_referral": lambda d: d["analyst_referral"],
    "committee_review_account_rules": lambda d: d["committee_review_account_rules"],
    "committee_site_aggregate_rule": lambda d: d["committee_site_aggregate_rule"],
    "administrative_warning_status": lambda d: d["administrative_warning_status"],
    "data_quality_review": lambda d: d["data_quality_review"],
    "risk_action_gate": lambda d: d["risk_action_gate"],
    "policy_applicability.mode": lambda d: d["policy_applicability"]["mode"],
    "policy_applicability.quarterly_exception_review": lambda d: d["policy_applicability"]["quarterly_exception_review"],
}

_THRESHOLDS = {
    "score_drop", "bin_drop", "consecutive_declines", "volatility",
}
_CRITERIA = _THRESHOLDS


def _expected(field: str, decision: dict) -> Any:
    """Get the authoritative value, or raise KeyError for an unsupported claim."""
    if field in _SIMPLE_FIELDS:
        return _SIMPLE_FIELDS[field](decision)
    if field.startswith("policy_applicability.thresholds."):
        name = field.rsplit(".", 1)[-1]
        if name in _THRESHOLDS:
            return decision["policy_applicability"]["thresholds"][name]
    if field.startswith("raw_event_count."):
        criterion = field.rsplit(".", 1)[-1]
        if criterion in _CRITERIA:
            # Events are dated within the window. A continuous streak may
            # produce more than one overlapping streak-ending event.
            return sum(e["criterion"] == criterion for e in decision["events"])
    raise KeyError(field)


def _same_value(claimed: Any, expected: Any) -> bool:
    """Compare types strictly: True must not count as the number 1."""
    if isinstance(expected, bool):
        return type(claimed) is bool and claimed == expected
    if isinstance(expected, (int, float)):
        return (type(claimed) in (int, float) and claimed == expected)
    return type(claimed) is str and claimed == expected


def audit_policy_claims(
    claims: list[dict],
    history: list[dict],
    *,
    account_id: int,
    as_of_period: str,
    site_onboarded_period: str | None = None,
    last_exception_review_period: str | None = None,
) -> dict:
    """Check explicit claims against independent policy computation.

    Claim shape: {"field": str, "value": ..., "account_id": int,
                  "as_of_period": "YYYY-MM-01", "market": int}.

    Contract: `history` must be the trusted result of get_history(account_id),
    not a narrative or dataset invented by the LLM. Account ID is not returned
    by the existing get_history rows, so the caller establishes provenance.

    An unsupported or malformed claim is UNCHECKED, not a pass. Wrong account,
    month, or market is a failure even if the asserted status happens to match.
    """
    if type(account_id) is not int:
        raise ValueError("account_id must be an integer")
    if not isinstance(claims, list):
        raise TypeError("claims must be a list of dictionaries")
    if any("account_id" in row and row["account_id"] != account_id
           for row in history):
        raise ValueError("History account_id does not match the audit account_id")

    decision = evaluate_policy_decision(
        history,
        as_of_period,
        site_onboarded_period=site_onboarded_period,
        last_exception_review_period=last_exception_review_period,
    )
    checks, unchecked = [], []
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict) or not all(
            key in claim for key in ("field", "value", "account_id", "as_of_period", "market")
        ):
            unchecked.append({"index": i, "claim": claim, "reason": "malformed_claim"})
            continue
        field = claim["field"]
        if not isinstance(field, str):
            unchecked.append({"index": i, "claim": claim, "reason": "unsupported_field"})
            continue
        try:
            expected = _expected(field, decision)
        except KeyError:
            unchecked.append({"index": i, "claim": claim, "reason": "unsupported_field"})
            continue
        context_matches = (
            type(claim["account_id"]) is int
            and claim["account_id"] == account_id
            and claim["as_of_period"] == decision["as_of_period"]
            and type(claim["market"]) is int
            and claim["market"] == decision["market"]
        )
        value_matches = _same_value(claim["value"], expected)
        checks.append({
            "field": field,
            "claimed": claim["value"],
            "expected": expected,
            "context_matches": context_matches,
            "value_matches": value_matches,
            "passed": context_matches and value_matches,
        })

    failed = any(not check["passed"] for check in checks)
    status = ("failed" if failed else
              "incomplete" if unchecked or not checks else "passed")
    return {
        "status": status,
        "scope": "explicit policy claims only (not a full free-text audit)",
        "account_id": account_id,
        "as_of_period": decision["as_of_period"],
        "market": decision["market"],
        "checks": checks,
        "unchecked_claims": unchecked,
        "evidence": {
            "window_start": decision["window_start"],
            "window_end": decision["window_end"],
            "events": decision["events"],
            "data_quality_flags": decision["data_quality_flags"],
            "policy_sources": decision["policy_sources"],
        },
    }


# A controlled format, not a general language understanding system.
_LABELS = {
    "warning signal": "warning_signal_status",
    "analyst referral": "analyst_referral",
    "committee review": "committee_review_account_rules",
    "data quality review": "data_quality_review",
    "risk action gate": "risk_action_gate",
    "market policy mode": "policy_applicability.mode",
}
_LABEL_LINE = re.compile(r"^([A-Za-z ]+):\s*([a-z][a-z_0-9]*)\s*$")


def audit_labeled_policy_answer(
    answer: str,
    history: list[dict],
    *,
    account_id: int,
    as_of_period: str,
    site_onboarded_period: str | None = None,
    last_exception_review_period: str | None = None,
) -> dict:
    """Audit only fixed 'Warning signal: triggered' style lines.

    Any other nonblank text makes the audit incomplete (or failed if a
    recognized claim is wrong). Never infer 'passed' for unknown prose.
    Account, period and market are taken from the trusted caller, NOT inferred
    from the text. General-answer attribution needs the older context guardrail.
    """
    # Validates history independently before claiming any status.
    claims, unchecked_lines = [], []
    market = None
    for row in history:
        if "market" in row:
            market = row["market"]
            break
    for line in answer.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _LABEL_LINE.fullmatch(line)
        field = _LABELS.get(match.group(1).lower()) if match else None
        if field is None:
            unchecked_lines.append(line)
        else:
            claims.append({
                "field": field, "value": match.group(2),
                "account_id": account_id, "as_of_period": as_of_period,
                "market": market,
            })

    result = audit_policy_claims(
        claims, history, account_id=account_id, as_of_period=as_of_period,
        site_onboarded_period=site_onboarded_period,
        last_exception_review_period=last_exception_review_period,
    )
    result["unchecked_text"] = unchecked_lines
    if unchecked_lines and result["status"] == "passed":
        result["status"] = "incomplete"
    return result
