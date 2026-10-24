"""Module 5.5.7.1: safely narrate a verified zero-decline, not-triggered case.

Scope is deliberately narrow: two grounded numerical claims plus two independently
passed policy claims, all bound to market 1 and a single reporting date. A zero
count says nothing about events *outside* the evaluated policy window.
"""

from src.narrative_composer import CompositionError, compose_verified_score_bin
from src.narrative_findings import FindingError, extract_verified_policy_findings
from src.temporal_policy_mapping import (
    TemporalPolicyMappingError, map_verified_decline_events,
)


class ZeroDeclineNarrativeError(ValueError):
    """The evidence cannot authorize a verified no-decline narrative."""


def compose_verified_no_decline_narrative(
    audit: dict, tool_evidence: list[dict], *,
    account_id: int, as_of_period: str, market: int,
) -> str:
    """Publish exactly score, bin, not_triggered warning and zero decline events.

    Assumes the trusted verification pipeline produced the passed internal audit;
    does not accept arbitrary user-authored audits as an evidence source. Other
    markets, warning values, positive counts, missing/extra claims all block.
    """
    if type(account_id) is not int or type(market) is not int or market != 1:
        raise ZeroDeclineNarrativeError("This report requires a market-1 account")
    if (
        not isinstance(audit, dict) or audit.get("status") != "passed"
        or not isinstance(audit.get("schema"), dict)
        or audit["schema"].get("status") != "passed"
        or audit.get("issues") or audit.get("limitations")
        or not isinstance(audit.get("narrative"), dict)
        or audit["narrative"].get("coverage_method") != "llm_draft_excluded"
    ):
        raise ZeroDeclineNarrativeError("A clean passed claim-first audit is required")

    numeric, policy = audit.get("numeric"), audit.get("policy")
    if (
        not isinstance(numeric, dict) or numeric.get("status") != "passed"
        or not isinstance(numeric.get("checks"), list) or len(numeric["checks"]) != 2
        or not isinstance(policy, dict) or policy.get("status") != "passed"
        or not isinstance(policy.get("checks"), list) or len(policy["checks"]) != 2
    ):
        raise ZeroDeclineNarrativeError("Exactly two numerical and two policy checks are required")

    by_field = {}
    for check in policy["checks"]:
        if not isinstance(check, dict) or check.get("status") != "passed":
            raise ZeroDeclineNarrativeError("An individual policy check is not passed")
        claim = check.get("claim")
        if not isinstance(claim, dict) or type(claim.get("field")) is not str:
            raise ZeroDeclineNarrativeError("A policy claim is malformed")
        if claim["field"] in by_field:
            raise ZeroDeclineNarrativeError("Duplicate policy claim")
        by_field[claim["field"]] = check

    if set(by_field) != {"warning_signal_status", "raw_event_count.consecutive_declines"}:
        raise ZeroDeclineNarrativeError("Unsupported policy claim set")
    warning, count = (by_field["warning_signal_status"],
                      by_field["raw_event_count.consecutive_declines"])
    if (warning["claim"].get("value") != "not_triggered"
            or type(warning["claim"].get("value")) is not str
            or type(count["claim"].get("value")) is not int
            or count["claim"]["value"] != 0):
        raise ZeroDeclineNarrativeError("Only not_triggered plus a verified zero count is supported")

    def policy_evidence(check: dict) -> dict:
        inner = check.get("audit")
        if (not isinstance(inner, dict) or inner.get("status") != "passed"
                or inner.get("unchecked_claims") != []):
            raise ZeroDeclineNarrativeError("Authoritative policy validation is missing")
        evidence = inner.get("evidence")
        required = {"window_start", "window_end", "events", "data_quality_flags", "policy_sources"}
        if not isinstance(evidence, dict) or not required.issubset(evidence):
            raise ZeroDeclineNarrativeError("Policy event provenance is incomplete")
        return evidence

    ev_warning, ev_count = policy_evidence(warning), policy_evidence(count)
    if ev_warning != ev_count:
        raise ZeroDeclineNarrativeError("Policy claims come from inconsistent evaluations")
    if (ev_count["events"] != [] or not isinstance(ev_count["policy_sources"], list)
            or "POL-002" not in ev_count["policy_sources"]
            or not isinstance(ev_count["data_quality_flags"], list)):
        raise ZeroDeclineNarrativeError("No-trigger window evidence is insufficient or contradictory")

    # The existing composer verifies exact, fully grounded score/bin observations.
    score_view = {**audit, "policy": {"status": "not_applicable", "checks": []}}
    # The finding adapter independently checks the policy status and caller context.
    warning_view = {
        **audit,
        "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "passed", "checks": [warning]},
    }
    count_view = {
        **audit,
        "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "passed", "checks": [count]},
    }
    try:
        score_text = compose_verified_score_bin(
            score_view, account_id=account_id, period=as_of_period,
        )
        # The score composer checks that both metrics use the SAME market;
        # now bind that market to the caller and separately checked policy context.
        if any(check["claim"].get("market") != market for check in numeric["checks"]):
            raise ZeroDeclineNarrativeError("Numerical market differs from caller market")
        findings = extract_verified_policy_findings(
            warning_view, account_id=account_id, as_of_period=as_of_period, market=market,
        )
        if (len(findings) != 1 or findings[0].kind != "policy"
                or findings[0].field != "warning_signal_status"
                or findings[0].value != "not_triggered"
                or findings[0].event_period is not None
                or findings[0].evidence_periods or findings[0].policy_ids):
            raise ZeroDeclineNarrativeError("Warning finding is not safely supported")
        mapped = map_verified_decline_events(
            count_view, tool_evidence, account_id=account_id,
            as_of_period=as_of_period, market=market,
        )
    except (CompositionError, FindingError, TemporalPolicyMappingError) as exc:
        raise ZeroDeclineNarrativeError("Verification evidence cannot support this report") from exc
    if mapped.raw_event_count != 0 or mapped.findings:
        raise ZeroDeclineNarrativeError("Verified decline events exist")
    if (mapped.window_start != ev_count["window_start"]
            or mapped.window_end != ev_count["window_end"]):
        raise ZeroDeclineNarrativeError("Policy windows disagree")

    return (
        score_text + "\n\n"
        + f"As of {as_of_period}, the policy-evaluated early-warning signal for "
          f"account {account_id} in market {market} was not triggered. "
          f"Within the POL-002 evaluation window from {mapped.window_start} "
          f"through {mapped.window_end}, no qualifying three-consecutive-month "
          "score-decline events were identified. "
          "This does not establish whether an operational warning was previously "
          "registered or cleared, or whether events occurred outside this window."
    )
