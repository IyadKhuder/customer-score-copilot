"""Module 5.4.3: fail-closed handling of structured-response audits.

This layer deliberately does NOT retry an LLM or change policy calculations.
It decides whether the narrative may be shown, and what kind of follow-up
is needed if existing verification was unsuccessful.

Typical usage:
    audit = verify_structured_response(raw_json, tool_evidence)
    decision = handle_verification(audit)
    print(decision["public_answer"])

IMPORTANT: a 'passed' audit means that supported checks passed, NOT that
arbitrary prose has undergone a complete semantic proof. The legacy
narrative coverage checker is best effort.
"""

from typing import Any

_VALID = {"passed", "failed", "incomplete"}
_SAFE_FALLBACKS = {
    "failed": (
        "I cannot provide this analysis as verified: one or more claims "
        "contradict the available evidence. The draft needs correction."
    ),
    "incomplete": (
        "I cannot verify this analysis with the available evidence. "
        "More information or a corrected response is needed."
    ),
}


def _section(audit: dict, name: str) -> dict:
    value = audit.get(name)
    return value if isinstance(value, dict) else {}


def _status(section: dict, *, optional: bool = False) -> str:
    allowed = _VALID | ({"not_applicable"} if optional else set())
    value = section.get("status")
    return value if isinstance(value, str) and value in allowed else "incomplete"


def _check_statuses(section: dict, name: str) -> tuple[list[str], bool]:
    """Get check-level statuses, with a consistency flag for 'passed' audits."""
    if section.get("status") == "not_applicable":
        return [], True
    checks = section.get("checks")
    if not isinstance(checks, list) or not checks:
        return [], False
    statuses = []
    consistent = True
    for check in checks:
        if not isinstance(check, dict):
            statuses.append("incomplete")
            consistent = False
            continue
        state = check.get("status")
        if not isinstance(state, str) or state not in _VALID:
            state = "incomplete"
            consistent = False
        # Verify a 'passed' check is backed by the validator's own verdict.
        if state == "passed" and name == "numeric":
            if check.get("context_grounded") is not True:
                state = "incomplete"
                consistent = False
        if state == "passed" and name == "policy":
            detail = check.get("audit")
            inner = detail.get("checks") if isinstance(detail, dict) else None
            if (
                not isinstance(detail, dict)
                or detail.get("status") != "passed"
                or not isinstance(inner, list)
                or len(inner) != 1
                or not isinstance(inner[0], dict)
                or inner[0].get("passed") is not True
            ):
                state = "incomplete"
                consistent = False
        statuses.append(state)
    return statuses, consistent


def handle_verification(audit: dict[str, Any]) -> dict[str, Any]:
    """Return a publication decision based only on a verification report.

    Never return unchecked LLM prose as public_answer. No external calls,
    no retries, and no fresh DB queries occur here.
    """
    if not isinstance(audit, dict):
        audit = {}

    schema = _section(audit, "schema")
    numeric = _section(audit, "numeric")
    policy = _section(audit, "policy")
    narrative = _section(audit, "narrative")

    schema_status = _status(schema)
    numeric_status = _status(numeric, optional=True)
    policy_status = _status(policy, optional=True)
    narrative_status = _status(narrative)

    reasons: list[str] = []
    check_states: list[str] = []
    for name, section, state in (
        ("numeric", numeric, numeric_status),
        ("policy", policy, policy_status),
    ):
        states, consistent = _check_statuses(section, name)
        check_states.extend(states)
        if state == "passed" and (not consistent or any(s != "passed" for s in states)):
            reasons.append("INCONSISTENT_CHECK_REPORT")
        if state == "not_applicable" and section.get("checks"):
            reasons.append("INCONSISTENT_CHECK_REPORT")

    if schema_status != "passed":
        reasons.append("SCHEMA_INVALID")
    if numeric_status == "failed" or "failed" in _check_statuses(numeric, "numeric")[0]:
        reasons.append("NUMERIC_MISMATCH")
    elif numeric_status == "incomplete" or "incomplete" in _check_statuses(numeric, "numeric")[0]:
        reasons.append("NUMERIC_UNVERIFIED")
    if policy_status == "failed" or "failed" in _check_statuses(policy, "policy")[0]:
        reasons.append("POLICY_MISMATCH")
    elif policy_status == "incomplete" or "incomplete" in _check_statuses(policy, "policy")[0]:
        reasons.append("POLICY_UNVERIFIED")

    if (
        narrative_status != "passed"
        or narrative.get("coverage_complete") is not True
        or narrative.get("omitted_score_claims")
        or narrative.get("unchecked_text")
    ):
        reasons.append("NARRATIVE_COVERAGE_GAP")

    checked_groups = sum(
        state != "not_applicable" for state in (numeric_status, policy_status)
    )
    if checked_groups == 0:
        reasons.append("NO_CHECKED_CLAIMS")

    raw_issues = audit.get("issues")
    if not isinstance(raw_issues, list):
        reasons.append("INVALID_REPORT")
    elif raw_issues:
        reasons.append("AUDIT_ISSUES")

    answer = audit.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        reasons.append("MISSING_ANSWER")

    if audit.get("status") not in _VALID:
        reasons.append("INVALID_REPORT")

    # A lower-level failure takes priority even if the reported aggregate
    # status is wrong. Never upgrade 'failed'/'incomplete' to 'passed'.
    observations = [audit.get("status"), schema_status, numeric_status,
                    policy_status, narrative_status, *check_states]
    if "failed" in observations:
        effective_status = "failed"
    elif (any(x in ("incomplete", None) for x in observations)
          or reasons):
        effective_status = "incomplete"
    else:
        effective_status = "passed"

    # If the overall report says passed but has conflicting internal facts,
    # fail closed; still retain an actual contradiction as 'failed'.
    if reasons and effective_status == "passed":
        effective_status = "incomplete"

    # Only an internally consistent, fully checked report is publishable.
    publishable = effective_status == "passed" and not reasons
    if publishable:
        return {
            "status": "passed",
            "publishable": True,
            "public_answer": answer,
            "next_action": "publish",
            "reason_codes": [],
        }

    if effective_status == "failed":
        next_action = "revise_response"
    elif "SCHEMA_INVALID" in reasons:
        next_action = "regenerate_json"
    elif _needs_history(audit):
        next_action = "gather_evidence"
    elif "POLICY_UNVERIFIED" in reasons:
        next_action = "manual_review"
    elif any(code in reasons for code in (
        "NARRATIVE_COVERAGE_GAP", "NO_CHECKED_CLAIMS", "AUDIT_ISSUES"
    )):
        next_action = "revise_response"
    else:
        next_action = "manual_review"

    # De-duplicate without changing the first occurrence's order.
    return {
        "status": effective_status,
        "publishable": False,
        "public_answer": _SAFE_FALLBACKS[effective_status],
        "next_action": next_action,
        "reason_codes": list(dict.fromkeys(reasons)),
    }


def _needs_history(audit: dict) -> bool:
    """Check if an incomplete claim can be addressed by fetching history."""
    for name in ("numeric", "policy"):
        section = _section(audit, name)
        checks = section.get("checks")
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, dict) or check.get("status") != "incomplete":
                continue
            if name == "numeric":
                # Module 5.4.2 treats a missing account/period observation
                # as 'incomplete' with no validator error.
                if "error" not in check:
                    return True
            elif name == "policy":
                detail = check.get("audit", {})
                if isinstance(detail, dict) and detail.get("reason") == (
                    "No get_history evidence for account and as_of_period"
                ):
                    return True
    return False
