"""Module 5.5.7.2 — policy-evaluated data-quality holds, without false clearance.

A verified 'pending_data_quality' outcome may be rendered for market 1 when
recorded score declines and POL-005 holds agree on the SAME dated events.
A verified 'insufficient_evidence' status is explicitly nonpublishable.

This is a composer over the INTERNAL verification audit, not a new policy engine.
"""

from src.narrative_composer import CompositionError, compose_verified_score_bin
from src.narrative_findings import FindingError, extract_verified_policy_findings
from src.temporal_narrative import (
    TemporalNarrativeError, attribute_verified_decline_events,
    compose_verified_decline_event_narrative,
)


class QualityHoldNarrativeError(ValueError):
    """No safe, complete data-quality-hold narrative can be rendered."""


class InsufficientPolicyEvidenceError(QualityHoldNarrativeError):
    """The policy evaluator verified that its evidence is insufficient."""


def compose_verified_quality_hold_narrative(
    audit: dict, tool_evidence: list[dict], *,
    account_id: int, as_of_period: str, market: int,
) -> str:
    """Render exactly score+bin+warning status+decline count, or block.

    Supported PUBLIC result: pending_data_quality + positive raw decline count,
    with all corresponding dated decline events awaiting POL-005 review.

    Supported NONPUBLIC diagnostic: insufficient_evidence + zero raw events.
    This raises InsufficientPolicyEvidenceError; the agent blocks publication.
    All other combinations, additional claims, contradictions and missing
    provenance raise QualityHoldNarrativeError.
    """
    if type(account_id) is not int or type(market) is not int or market != 1:
        raise QualityHoldNarrativeError("Only caller-selected market 1 is supported")
    if (not isinstance(audit, dict) or audit.get("status") != "passed"
            or not isinstance(audit.get("schema"), dict)
            or audit["schema"].get("status") != "passed"
            or audit.get("issues")
            or not isinstance(audit.get("narrative"), dict)
            or audit["narrative"].get("coverage_method") != "llm_draft_excluded"):
        raise QualityHoldNarrativeError("A clean passed claim-first audit is required")

    numeric, policy = audit.get("numeric"), audit.get("policy")
    if (not isinstance(numeric, dict) or numeric.get("status") != "passed"
            or not isinstance(numeric.get("checks"), list)
            or len(numeric["checks"]) != 2
            or not isinstance(policy, dict) or policy.get("status") != "passed"
            or not isinstance(policy.get("checks"), list)
            or len(policy["checks"]) != 2):
        raise QualityHoldNarrativeError("Exactly two numerical and two policy checks are required")

    by_field = {}
    for check in policy["checks"]:
        if not isinstance(check, dict) or check.get("status") != "passed":
            raise QualityHoldNarrativeError("A policy check did not pass")
        claim = check.get("claim")
        if not isinstance(claim, dict) or type(claim.get("field")) is not str:
            raise QualityHoldNarrativeError("A policy claim is malformed")
        if claim["field"] in by_field:
            raise QualityHoldNarrativeError("Duplicate policy claim")
        by_field[claim["field"]] = check

    if set(by_field) != {"warning_signal_status", "raw_event_count.consecutive_declines"}:
        raise QualityHoldNarrativeError("Unsupported policy claim set")
    warning = by_field["warning_signal_status"]
    count = by_field["raw_event_count.consecutive_declines"]
    status = warning["claim"].get("value")
    raw_count = count["claim"].get("value")
    if type(status) is not str or type(raw_count) is not int:
        raise QualityHoldNarrativeError("Incorrect policy value types")
    if status not in ("pending_data_quality", "insufficient_evidence"):
        raise QualityHoldNarrativeError("This composer handles only policy holds or evidence gaps")
    if raw_count < 0 or (status == "insufficient_evidence" and raw_count != 0):
        raise QualityHoldNarrativeError("Policy status and event count are inconsistent")
    if status == "pending_data_quality" and raw_count == 0:
        raise QualityHoldNarrativeError("A data-quality hold requires at least one raw decline event")

    # Limitations always prohibit PUBLIC composition. The one exception below
    # is not a publication exception: a separately verified insufficient_evidence
    # decision must be classified as a specific gather-evidence BLOCK even if
    # the model truthfully reports the missing baseline in its limitations.
    if audit.get("limitations") and status != "insufficient_evidence":
        raise QualityHoldNarrativeError("Unresolved limitations prevent publication")

    def policy_evidence(check: dict) -> dict:
        inner = check.get("audit")
        if (not isinstance(inner, dict) or inner.get("status") != "passed"
                or inner.get("unchecked_claims") != []
                or (inner.get("account_id"), inner.get("as_of_period"), inner.get("market"))
                   != (account_id, as_of_period, market)):
            raise QualityHoldNarrativeError("Authoritative policy audit context is missing")
        verdicts = inner.get("checks")
        claim = check["claim"]
        if (not isinstance(verdicts, list) or len(verdicts) != 1
                or not isinstance(verdicts[0], dict)
                or verdicts[0].get("passed") is not True
                or verdicts[0].get("context_matches") is not True
                or verdicts[0].get("value_matches") is not True
                or verdicts[0].get("field") != claim["field"]
                or type(verdicts[0].get("expected")) is not type(claim["value"])
                or verdicts[0].get("expected") != claim["value"]
                or verdicts[0].get("claimed") != claim["value"]):
            raise QualityHoldNarrativeError("Policy field was not independently confirmed")
        evidence = inner.get("evidence")
        if (not isinstance(evidence, dict)
                or not {"window_start", "window_end", "events", "data_quality_flags", "policy_sources"}.issubset(evidence)):
            raise QualityHoldNarrativeError("Policy event provenance is unavailable")
        return evidence

    ev_warning, ev_count = policy_evidence(warning), policy_evidence(count)
    if ev_warning != ev_count:
        raise QualityHoldNarrativeError("Policy claims came from different evaluations")
    if (not isinstance(ev_count["events"], list)
            or not isinstance(ev_count["data_quality_flags"], list)
            or not isinstance(ev_count["policy_sources"], list)
            or "POL-002" not in ev_count["policy_sources"]):
        raise QualityHoldNarrativeError("Policy event provenance is incomplete")

    # Verify numerical observations using the original, unchanged numeric composer.
    score_view = {**audit, "policy": {"status": "not_applicable", "checks": []}}
    if status == "insufficient_evidence" and audit.get("limitations"):
        # Only for a NONPUBLIC cross-check of the independently verified numeric
        # claims. compose_verified_score_bin otherwise rejects all limitations.
        # Its result is never returned: this branch unconditionally raises the
        # dedicated evidence-gap exception after full provenance checks.
        score_view = {**score_view, "limitations": []}
    warning_view = {
        **audit, "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "passed", "checks": [warning]},
    }
    if status == "insufficient_evidence" and audit.get("limitations"):
        # As above: reuse the existing validated-finding adapter for the
        # NONPUBLIC classification, never to publish a narrative with limits.
        warning_view = {**warning_view, "limitations": []}
    count_view = {
        **audit, "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "passed", "checks": [count]},
    }
    try:
        score_text = compose_verified_score_bin(
            score_view, account_id=account_id, period=as_of_period,
        )
        if any(type(c["claim"].get("market")) is not int
               or c["claim"]["market"] != market for c in numeric["checks"]):
            raise QualityHoldNarrativeError("Numerical claims disagree with caller market")
        findings = extract_verified_policy_findings(
            warning_view, account_id=account_id,
            as_of_period=as_of_period, market=market,
        )
    except (CompositionError, FindingError) as exc:
        raise QualityHoldNarrativeError("Policy or numerical verification does not support this report") from exc
    if (len(findings) != 1 or findings[0].field != "warning_signal_status"
            or findings[0].value != status):
        raise QualityHoldNarrativeError("Verified warning finding differs from the report")

    if status == "insufficient_evidence":
        if ev_count["events"] != []:
            raise QualityHoldNarrativeError("Insufficient-evidence case has contradictory raw events")
        # A passed policy claim of 'insufficient_evidence' does not grant
        # permission to publish a 'not_triggered' or 'cleared' report.
        raise InsufficientPolicyEvidenceError(
            "The verified policy evaluation has insufficient historical coverage"
        )

    # Pending DQ: require matched, individually dated decline events and
    # independently checked POL-005 flags. All policy events must be pending,
    # since the overall warning status is pending rather than triggered.
    if ("POL-005" not in ev_count["policy_sources"]
            or not ev_count["data_quality_flags"]
            or not ev_count["events"]
            or any(not isinstance(e, dict) or e.get("review_state") != "pending_data_quality"
                   for e in ev_count["events"])):
        raise QualityHoldNarrativeError("Pending policy status conflicts with data-quality event state")
    try:
        dated = attribute_verified_decline_events(
            count_view, tool_evidence, account_id=account_id,
            as_of_period=as_of_period, market=market,
        )
        if len(dated) != raw_count or any(
            event.review_state != "pending_data_quality" for event in dated
        ):
            raise QualityHoldNarrativeError("A decline event is not under verified data-quality review")
        event_text = compose_verified_decline_event_narrative(
            count_view, tool_evidence, account_id=account_id,
            as_of_period=as_of_period, market=market,
        )
    except TemporalNarrativeError as exc:
        raise QualityHoldNarrativeError("Recorded decline events and policy holds disagree") from exc

    return (
        score_text + "\n\n"
        + f"As of {as_of_period}, the policy-evaluated early-warning signal "
          f"for account {account_id} in market {market} is pending data-quality "
          "review; this is not a verified absence of early-warning events.\n\n"
        + event_text
    )
