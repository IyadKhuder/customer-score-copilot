"""Module 5.5.7.3 — narrow, fail-closed POL-004 grace-period event renderer.

This reports dated FOUR-transition declines, not an administrative warning,
not a full score report, and not any risk action. The applicable threshold and
quarterly exception-review status come from the independently verified policy
claims, computed using trusted caller-provided site metadata.
"""

from datetime import date

from src.temporal_evidence import (
    TemporalEvidenceError, detect_consecutive_declines,
)


class GraceDeclineNarrativeError(ValueError):
    """Audit, score evidence, or policy exception is not safe to publish."""


_REQUIRED = {
    "policy_applicability.mode",
    "policy_applicability.thresholds.consecutive_declines",
    "policy_applicability.quarterly_exception_review",
    "raw_event_count.consecutive_declines",
}


def _iso_month(value: object) -> str:
    if type(value) is not str:
        raise GraceDeclineNarrativeError("Expected a canonical reporting month")
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed.day != 1:
            raise ValueError("not first of calendar month")
    except ValueError as exc:
        raise GraceDeclineNarrativeError("Invalid first-of-month ISO date") from exc
    return value


def _month(period: str) -> int:
    dt = date.fromisoformat(period)
    return 12 * dt.year + dt.month


def _verified_event_data(audit: dict, *, account_id: int, as_of_period: str,
                         market: int) -> tuple[dict, int]:
    if (not isinstance(audit, dict) or audit.get("status") != "passed"
            or audit.get("schema", {}).get("status") != "passed"
            or audit.get("issues") or audit.get("limitations")):
        raise GraceDeclineNarrativeError("Overall audit did not pass cleanly")
    if audit.get("narrative", {}).get("coverage_method") != "llm_draft_excluded":
        raise GraceDeclineNarrativeError("Only claim-first audits may be rendered")
    numeric = audit.get("numeric")
    if (not isinstance(numeric, dict) or numeric.get("status") != "not_applicable"
            or numeric.get("checks") != []):
        raise GraceDeclineNarrativeError("This scope cannot omit numerical claims")
    policy = audit.get("policy")
    if not isinstance(policy, dict) or policy.get("status") != "passed":
        raise GraceDeclineNarrativeError("Policy audit must pass")
    checks = policy.get("checks")
    if not isinstance(checks, list) or len(checks) != 4:
        raise GraceDeclineNarrativeError("Exactly four policy claims are required")

    verified = {}
    common_evidence = None
    for check in checks:
        if not isinstance(check, dict) or check.get("status") != "passed":
            raise GraceDeclineNarrativeError("Unverified policy check")
        claim, inner = check.get("claim"), check.get("audit")
        if not isinstance(claim, dict) or not isinstance(inner, dict):
            raise GraceDeclineNarrativeError("Missing claim or validator result")
        if (set(claim) != {"field", "value", "account_id", "as_of_period", "market"}
                or claim["field"] not in _REQUIRED or claim["field"] in verified
                or type(claim["account_id"]) is not int or type(claim["market"]) is not int
                or (claim["account_id"], claim["as_of_period"], claim["market"])
                != (account_id, as_of_period, market)):
            raise GraceDeclineNarrativeError("Unsupported, duplicate or mismatched policy claim")
        if (inner.get("status") != "passed"
                or inner.get("unchecked_claims") != []
                or (inner.get("account_id"), inner.get("as_of_period"), inner.get("market"))
                != (account_id, as_of_period, market)):
            raise GraceDeclineNarrativeError("Policy evaluator context mismatch")
        inner_checks = inner.get("checks")
        if not isinstance(inner_checks, list) or len(inner_checks) != 1:
            raise GraceDeclineNarrativeError("Missing individual policy verdict")
        verdict = inner_checks[0]
        if (not isinstance(verdict, dict) or verdict.get("passed") is not True
                or verdict.get("context_matches") is not True
                or verdict.get("value_matches") is not True
                or verdict.get("field") != claim["field"]
                or type(verdict.get("claimed")) is not type(claim["value"])
                or type(verdict.get("expected")) is not type(claim["value"])
                or verdict["claimed"] != claim["value"]
                or verdict["expected"] != claim["value"]):
            raise GraceDeclineNarrativeError("Policy verdict does not confirm claim value")
        evidence = inner.get("evidence")
        if not isinstance(evidence, dict):
            raise GraceDeclineNarrativeError("Dated policy evidence is missing")
        if common_evidence is None:
            common_evidence = evidence
        elif common_evidence != evidence:
            raise GraceDeclineNarrativeError("Policy evaluations have conflicting evidence")
        verified[claim["field"]] = claim["value"]

    if set(verified) != _REQUIRED:
        raise GraceDeclineNarrativeError("Incomplete supported claim set")
    if (verified["policy_applicability.mode"] != "grace"
            or type(verified["policy_applicability.thresholds.consecutive_declines"]) is not int
            or verified["policy_applicability.thresholds.consecutive_declines"] != 4
            or verified["policy_applicability.quarterly_exception_review"] != "current"
            or type(verified["raw_event_count.consecutive_declines"]) is not int
            or verified["raw_event_count.consecutive_declines"] < 1):
        raise GraceDeclineNarrativeError("Unsupported grace threshold, review state, or event count")
    return common_evidence, verified["raw_event_count.consecutive_declines"]


def compose_verified_grace_declines(
    audit: dict, tool_evidence: list[dict], *,
    account_id: int, as_of_period: str, market: int,
) -> str:
    """Report individually matched four-decline events for reviewed grace markets.

    No operational registration, risk-action authorization, or warning clearance
    is inferred from the raw events. A no-events report is not in this scope.
    """
    if type(account_id) is not int or type(market) is not int or market == 1:
        raise GraceDeclineNarrativeError("A non-primary market and account are required")
    as_of_period = _iso_month(as_of_period)
    evidence, count = _verified_event_data(
        audit, account_id=account_id, as_of_period=as_of_period, market=market,
    )
    start, end = _iso_month(evidence.get("window_start")), _iso_month(evidence.get("window_end"))
    if end != as_of_period or _month(end) - _month(start) != 2:
        raise GraceDeclineNarrativeError("Incorrect rolling policy evaluation window")
    sources = evidence.get("policy_sources")
    if (not isinstance(sources, list) or
            not {"POL-002", "POL-004"}.issubset(set(sources))):
        raise GraceDeclineNarrativeError("POL-002/POL-004 attribution is missing")
    events, flags = evidence.get("events"), evidence.get("data_quality_flags")
    if not isinstance(events, list) or not isinstance(flags, list):
        raise GraceDeclineNarrativeError("Policy events or quality flags are missing")
    affected = set()
    for flag in flags:
        if (not isinstance(flag, dict) or flag.get("policy") != "POL-005"
                or not isinstance(flag.get("affected_periods"), list)
                or not flag["affected_periods"]
                or any(type(p) is not str for p in flag["affected_periods"])):
            raise GraceDeclineNarrativeError("Invalid data-quality flag")
        affected.update(flag["affected_periods"])

    try:
        observed = detect_consecutive_declines(
            tool_evidence, account_id=account_id, as_of_period=as_of_period,
            market=market, transitions=4,
        )
    except TemporalEvidenceError as exc:
        raise GraceDeclineNarrativeError("Recorded score history is insufficient") from exc
    observed = {(ev.event_period, ev.evidence_periods): ev
                for ev in observed if start <= ev.event_period <= end}
    if len(observed) != count:
        raise GraceDeclineNarrativeError("Policy event count differs from recorded score events")

    recognized = {}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("criterion"), str):
            raise GraceDeclineNarrativeError("Malformed policy event")
        if event["criterion"] != "consecutive_declines":
            continue
        supporting = event.get("evidence_periods")
        if (not isinstance(supporting, list) or len(supporting) != 5
                or any(type(p) is not str for p in supporting)
                or type(event.get("period")) is not str):
            raise GraceDeclineNarrativeError("Four-decline event lacks five observations")
        key = (event["period"], tuple(supporting))
        if key not in observed or key in recognized:
            raise GraceDeclineNarrativeError("Dated POL-004 event does not match recorded scores")
        flagged = bool(set(supporting) & affected)
        state = event.get("review_state")
        if (state not in ("eligible", "pending_data_quality")
                or (state == "pending_data_quality") != flagged):
            raise GraceDeclineNarrativeError("Review state contradicts data-quality flags")
        if flagged and "POL-005" not in sources:
            raise GraceDeclineNarrativeError("Missing POL-005 source for held event")
        recognized[key] = state

    if len(recognized) != len(observed):
        raise GraceDeclineNarrativeError("Missing or duplicated dated policy events")

    lines = [
        f"For account {account_id} in market {market}, the POL-002 evaluation "
        f"window as of {as_of_period} runs from {start} to {end}. "
        "The POL-004 grace-period exception uses four consecutive monthly "
        "declines; the policy evaluator reports a current quarterly exception review."
    ]
    for event_period, supporting in sorted(recognized):
        statement = (
            "Four consecutive monthly score declines were observed "
            f"from the baseline on {supporting[0]} through {event_period}. "
        )
        if recognized[(event_period, supporting)] == "eligible":
            statement += (
                "This policy-evaluated decline event is eligible for "
                "early-warning consideration under POL-002/POL-004."
            )
        else:
            statement += (
                "This event remains pending data-quality review under POL-005 "
                "and is not yet eligible for escalation."
            )
        lines.append(statement)
    lines.append(
        "This assessment does not confirm that an operational warning was "
        "registered or cleared, that the decline continues in the reporting "
        "month, or that a credit action has been authorized."
    )
    return "\n\n".join(lines)
