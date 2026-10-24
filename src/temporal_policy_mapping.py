"""Module 5.5.6.2: reconcile observed decline events with a passed policy audit.

This is an evidence adapter, NOT a policy evaluator or narrative publisher.
It deliberately cannot assert warning activation, referral, clearance, or policy
citations. The only policy claim supported is the existing validator field
``raw_event_count.consecutive_declines``.
"""

from dataclasses import dataclass
from datetime import date

from src.narrative_findings import VerifiedFinding
from src.temporal_evidence import (
    TemporalEvidenceError,
    detect_three_consecutive_declines,
)


class TemporalPolicyMappingError(ValueError):
    """The available checks do not safely support the event/policy mapping."""


@dataclass(frozen=True)
class TemporalPolicyMapping:
    """Verified count agreement plus the supporting, date-specific observations."""

    account_id: int
    as_of_period: str
    market: int
    window_start: str
    window_end: str
    raw_event_count: int
    findings: tuple[VerifiedFinding, ...]


def _iso(value: object) -> str:
    if type(value) is not str:
        raise TemporalPolicyMappingError("Window/reporting date must be an ISO string")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("non-canonical")
    except ValueError as exc:
        raise TemporalPolicyMappingError("Invalid ISO date in temporal policy mapping") from exc
    return value


def map_verified_decline_events(
    audit: dict, tool_evidence: list[dict], *,
    account_id: int, as_of_period: str, market: int,
) -> TemporalPolicyMapping:
    """Check temporal event dates against the policy evaluator's RAW event count.

    Requires exactly ONE independently passed policy claim:
    ``raw_event_count.consecutive_declines``. The validator audit must provide
    its window and raw events. All historic events are detected again from the
    agent's *recorded* get_history results, not from LLM-generated dates.

    The resulting findings are TEMPORAL observations only. Matching raw counts
    does not prove an effective warning, data-quality clearance, or policy
    applicability of a particular action.
    """
    if type(account_id) is not int or type(market) is not int:
        raise TemporalPolicyMappingError("Caller must supply integer account and market IDs")
    as_of_period = _iso(as_of_period)
    if not isinstance(audit, dict) or audit.get("status") != "passed":
        raise TemporalPolicyMappingError("Overall verification audit did not pass")
    if (
        not isinstance(audit.get("schema"), dict)
        or audit["schema"].get("status") != "passed"
        or audit.get("issues")
        or audit.get("limitations")
        or not isinstance(audit.get("narrative"), dict)
        or audit["narrative"].get("coverage_method") != "llm_draft_excluded"
    ):
        raise TemporalPolicyMappingError("Only clean claim-first audits can be mapped")

    numeric = audit.get("numeric")
    if not isinstance(numeric, dict):
        raise TemporalPolicyMappingError("Missing numerical audit")
    if numeric.get("status") == "not_applicable":
        if numeric.get("checks") != []:
            raise TemporalPolicyMappingError("Unexpected numerical checks")
    elif numeric.get("status") == "passed":
        checks = numeric.get("checks")
        if not isinstance(checks, list) or not checks or any(
            not isinstance(c, dict)
            or c.get("status") != "passed"
            or c.get("context_grounded") is not True
            or c.get("value_grounded") is not True
            or not isinstance(c.get("claim"), dict)
            or type(c["claim"].get("account_id")) is not int
            or type(c["claim"].get("market")) is not int
            or (c["claim"]["account_id"], c["claim"].get("period"), c["claim"]["market"])
            != (account_id, as_of_period, market)
            for c in checks
        ):
            raise TemporalPolicyMappingError("Numerical checks are not all grounded in caller context")
    else:
        raise TemporalPolicyMappingError("Numerical audit is incomplete or failed")

    policy = audit.get("policy")
    if not isinstance(policy, dict) or policy.get("status") != "passed":
        raise TemporalPolicyMappingError("Policy checks must all pass")
    checks = policy.get("checks")
    if not isinstance(checks, list) or len(checks) != 1:
        raise TemporalPolicyMappingError("Exactly one raw-event-count claim is supported")
    check = checks[0]
    if not isinstance(check, dict) or check.get("status") != "passed":
        raise TemporalPolicyMappingError("Policy claim did not pass")
    claim, inner = check.get("claim"), check.get("audit")
    if not isinstance(claim, dict) or not isinstance(inner, dict):
        raise TemporalPolicyMappingError("Policy claim or authoritative audit missing")
    if (
        set(claim) != {"field", "value", "account_id", "as_of_period", "market"}
        or claim["field"] != "raw_event_count.consecutive_declines"
        or type(claim["value"]) is not int
        or claim["value"] < 0
        or type(claim["account_id"]) is not int
        or type(claim["market"]) is not int
        or (claim["account_id"], claim["as_of_period"], claim["market"])
        != (account_id, as_of_period, market)
    ):
        raise TemporalPolicyMappingError("Unsupported or mismatched raw-event-count claim")
    if (
        inner.get("status") != "passed"
        or type(inner.get("account_id")) is not int
        or type(inner.get("market")) is not int
        or (inner["account_id"], inner.get("as_of_period"), inner["market"])
        != (account_id, as_of_period, market)
        or inner.get("unchecked_claims") != []
        or not isinstance(inner.get("checks"), list)
        or len(inner["checks"]) != 1
    ):
        raise TemporalPolicyMappingError("Authoritative policy audit is missing or inconsistent")
    verdict = inner["checks"][0]
    if (
        not isinstance(verdict, dict)
        or verdict.get("passed") is not True
        or verdict.get("context_matches") is not True
        or verdict.get("value_matches") is not True
        or verdict.get("field") != claim["field"]
        or type(verdict.get("expected")) is not int
        or verdict["expected"] != claim["value"]
        or verdict.get("claimed") != claim["value"]
    ):
        raise TemporalPolicyMappingError("Validator verdict does not confirm the raw event count")

    policy_evidence = inner.get("evidence")
    if not isinstance(policy_evidence, dict):
        raise TemporalPolicyMappingError("Policy evidence is unavailable")
    start, end = _iso(policy_evidence.get("window_start")), _iso(policy_evidence.get("window_end"))
    if not start <= end <= as_of_period or end != as_of_period:
        raise TemporalPolicyMappingError("Policy window is inconsistent with reporting date")
    policy_events = policy_evidence.get("events")
    if not isinstance(policy_events, list) or any(
        not isinstance(ev, dict) or not isinstance(ev.get("criterion"), str)
        for ev in policy_events
    ):
        raise TemporalPolicyMappingError("Policy event list is malformed")
    counted = sum(ev["criterion"] == "consecutive_declines" for ev in policy_events)
    if counted != claim["value"]:
        raise TemporalPolicyMappingError("Policy event list disagrees with passed raw count")

    try:
        observed = detect_three_consecutive_declines(
            tool_evidence, account_id=account_id,
            as_of_period=as_of_period, market=market,
        )
    except TemporalEvidenceError as exc:
        raise TemporalPolicyMappingError("Recorded score evidence is insufficient or inconsistent") from exc
    matching = tuple(ev for ev in observed if start <= ev.event_period <= end)
    if len(matching) != counted:
        raise TemporalPolicyMappingError("Policy and temporal detectors disagree on window event count")

    findings = tuple(
        VerifiedFinding(
            kind="temporal", field="consecutive_score_declines", value=3,
            account_id=account_id, as_of_period=as_of_period, market=market,
            event_period=ev.event_period, evidence_periods=ev.evidence_periods,
            # Do not add a policy ID or policy-action conclusion from a raw count.
            policy_ids=(),
        )
        for ev in matching
    )
    return TemporalPolicyMapping(
        account_id=account_id, as_of_period=as_of_period, market=market,
        window_start=start, window_end=end, raw_event_count=counted,
        findings=findings,
    )
