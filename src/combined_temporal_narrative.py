"""Module 5.5.6.5: compose a complete, verified score/policy/temporal answer.

This module is a *composer*, not a new verifier. It accepts only the audit
returned by the verification pipeline and the recorded tool-call evidence.
It reuses the two previously tested renderers with restricted, temporary views
of the **same fully passed** audit; neither view becomes publication authority.
"""

from src.narrative_composer import (
    CompositionError,
    compose_verified_score_bin_warning_signal,
)
from src.temporal_narrative import (
    TemporalNarrativeError,
    compose_verified_decline_event_narrative,
)


class CombinedTemporalNarrativeError(ValueError):
    """The full verified claim set cannot be rendered without omission."""


def compose_verified_full_narrative(
    audit: dict, tool_evidence: list[dict], *,
    account_id: int, as_of_period: str, market: int,
) -> str:
    """Render exactly score + bin + warning signal + decline-event count.

    Market 1 only; the three-decline detector is not calibrated for grace markets.
    The claimed warning status must be 'triggered' and at least one dated decline
    event must be validated. This does not assert that the decline event was
    necessarily the *only* reason for a warning, or that an operational record
    exists. Extra/unverified claims or conflicting policy evidence block output.
    """
    if type(account_id) is not int or type(market) is not int or market != 1:
        raise CombinedTemporalNarrativeError("A market-1 account context is required")
    if not isinstance(audit, dict) or audit.get("status") != "passed":
        raise CombinedTemporalNarrativeError("Full verification must pass")
    if not isinstance(audit.get("schema"), dict) or audit["schema"].get("status") != "passed":
        raise CombinedTemporalNarrativeError("Structured schema verification is required")
    if audit.get("issues") or audit.get("limitations"):
        raise CombinedTemporalNarrativeError("Unresolved audit issues or limitations")
    if (not isinstance(audit.get("narrative"), dict)
            or audit["narrative"].get("coverage_method") != "llm_draft_excluded"):
        raise CombinedTemporalNarrativeError("Only claim-first audits can be rendered")

    numeric, policy = audit.get("numeric"), audit.get("policy")
    if (not isinstance(numeric, dict) or numeric.get("status") != "passed"
            or not isinstance(numeric.get("checks"), list)
            or len(numeric["checks"]) != 2
            or not isinstance(policy, dict) or policy.get("status") != "passed"
            or not isinstance(policy.get("checks"), list)
            or len(policy["checks"]) != 2):
        raise CombinedTemporalNarrativeError("Require exactly two numerical and two policy checks")

    by_field = {}
    for check in policy["checks"]:
        if not isinstance(check, dict) or check.get("status") != "passed":
            raise CombinedTemporalNarrativeError("An individual policy check did not pass")
        claim = check.get("claim")
        if not isinstance(claim, dict) or not isinstance(claim.get("field"), str):
            raise CombinedTemporalNarrativeError("Policy claim is malformed")
        field = claim["field"]
        if field in by_field:
            raise CombinedTemporalNarrativeError("Duplicated policy field")
        by_field[field] = check

    if set(by_field) != {"warning_signal_status", "raw_event_count.consecutive_declines"}:
        raise CombinedTemporalNarrativeError("Unsupported or missing policy claims")

    # Two separately validated policy claims must describe the *same* policy
    # evaluation. Do not merge a window, event list, or DQ status from different
    # executions even if both audits independently say 'passed'.
    def evidence_of(check):
        inner = check.get("audit")
        if not isinstance(inner, dict) or inner.get("status") != "passed":
            raise CombinedTemporalNarrativeError("Missing authoritative policy audit")
        evidence = inner.get("evidence")
        required = ("window_start", "window_end", "events", "data_quality_flags", "policy_sources")
        if (not isinstance(evidence, dict)
                or any(key not in evidence for key in required)):
            raise CombinedTemporalNarrativeError("Policy event provenance is incomplete")
        return {key: evidence[key] for key in required}

    if evidence_of(by_field["warning_signal_status"]) != evidence_of(
            by_field["raw_event_count.consecutive_declines"]):
        raise CombinedTemporalNarrativeError("Policy checks disagree on event evidence")

    # First reuse the existing score+bin+warning composer. It independently
    # checks account, date, market, all numerical grounding, and signal value.
    warning_view = {
        **audit,
        "policy": {"status": "passed", "checks": [by_field["warning_signal_status"]]},
    }
    # Then reuse the *existing* dated-event composer. Its count, event dates,
    # evidence periods, and DQ review states are checked against tool outputs.
    decline_view = {
        **audit,
        "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "passed", "checks": [by_field["raw_event_count.consecutive_declines"]]},
    }
    try:
        snapshot_and_signal = compose_verified_score_bin_warning_signal(
            warning_view, account_id=account_id, period=as_of_period, market=market,
        )
        event_narrative = compose_verified_decline_event_narrative(
            decline_view, tool_evidence, account_id=account_id,
            as_of_period=as_of_period, market=market,
        )
    except (CompositionError, TemporalNarrativeError) as exc:
        raise CombinedTemporalNarrativeError("Some required findings cannot be rendered") from exc

    return snapshot_and_signal + "\n\n" + event_narrative
