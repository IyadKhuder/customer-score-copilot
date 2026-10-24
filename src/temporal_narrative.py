"""Module 5.5.6.3: deterministic, policy-attributed decline-event narratives.

This is NOT an agent response-gate integration. It requires a passed internal
claim-first audit and the exact tool evidence used to verify that audit.
It cannot authorize official administrative statuses or risk actions.
"""

from dataclasses import dataclass
from typing import Literal

from src.temporal_policy_mapping import (
    TemporalPolicyMappingError,
    map_verified_decline_events,
)


class TemporalNarrativeError(ValueError):
    """Recorded evidence or policy audit cannot safely support this narrative."""


@dataclass(frozen=True)
class AttributedDeclineEvent:
    """An event whose date and evidence are tied to a policy-evaluator record."""

    event_period: str
    evidence_periods: tuple[str, ...]
    review_state: Literal["eligible", "pending_data_quality"]


def attribute_verified_decline_events(
    audit: dict, tool_evidence: list[dict], *,
    account_id: int, as_of_period: str, market: int,
) -> tuple[AttributedDeclineEvent, ...]:
    """Reconcile individually dated POL-002 records with observed score events.

    The existing count-only mapping is necessary but insufficient for policy
    attribution: equal totals do not establish which events were recognized or
    whether any were put on hold for data-quality review. Here we require an
    exact event-period AND evidence-periods match, and an explicit review state.

    Scope: market 1, whose standard POL-002 threshold is three declines.
    Grace-period markets can require four declines under POL-004 and need a
    threshold-aware detector; we must not reuse our three-decline detector there.
    """
    # This standalone renderer cannot silently discard verified numeric claims.
    numeric = audit.get("numeric") if isinstance(audit, dict) else None
    if (not isinstance(numeric, dict)
            or numeric.get("status") != "not_applicable"
            or numeric.get("checks") != []):
        raise TemporalNarrativeError(
            "Numerical claims require a combined composer; none may be omitted"
        )
    if type(market) is not int or market != 1:
        raise TemporalNarrativeError(
            "Only standard-threshold market 1 is supported by this narrative"
        )
    try:
        mapping = map_verified_decline_events(
            audit, tool_evidence, account_id=account_id,
            as_of_period=as_of_period, market=market,
        )
    except TemporalPolicyMappingError as exc:
        raise TemporalNarrativeError("Decline-event count or evidence is not verified") from exc

    if not mapping.findings:
        raise TemporalNarrativeError("No verified decline event to narrate")

    # map_verified_decline_events has already checked the full audit, including
    # the sole permitted raw_event_count.consecutive_declines claim.
    policy_evidence = audit["policy"]["checks"][0]["audit"]["evidence"]
    sources = policy_evidence.get("policy_sources")
    if not isinstance(sources, list) or "POL-002" not in sources:
        raise TemporalNarrativeError("POL-002 source attribution is unavailable")

    policy_events = policy_evidence["events"]
    decline_records = [e for e in policy_events if e["criterion"] == "consecutive_declines"]
    observed_by_key = {
        (f.event_period, f.evidence_periods): f for f in mapping.findings
    }
    if len(observed_by_key) != len(mapping.findings):
        raise TemporalNarrativeError("Duplicate temporal observations")

    flags = policy_evidence.get("data_quality_flags")
    if not isinstance(flags, list):
        raise TemporalNarrativeError("Data-quality evidence is unavailable")
    affected_periods = set()
    for flag in flags:
        if (
            not isinstance(flag, dict)
            or flag.get("policy") != "POL-005"
            or not isinstance(flag.get("affected_periods"), list)
            or not flag["affected_periods"]
            or any(type(p) is not str for p in flag["affected_periods"])
        ):
            raise TemporalNarrativeError("Malformed data-quality evidence")
        affected_periods.update(flag["affected_periods"])

    recognized: dict[tuple[str, tuple[str, ...]], AttributedDeclineEvent] = {}
    for event in decline_records:
        period, supporting = event.get("period"), event.get("evidence_periods")
        if (
            type(period) is not str
            or not isinstance(supporting, list)
            or len(supporting) != 4
            or any(type(p) is not str for p in supporting)
            or supporting[-1] != period
        ):
            raise TemporalNarrativeError("Policy event lacks dated supporting evidence")
        key = (period, tuple(supporting))
        if key not in observed_by_key or key in recognized:
            raise TemporalNarrativeError("Policy event identity disagrees with score evidence")
        review_state = event.get("review_state")
        if review_state not in ("eligible", "pending_data_quality"):
            raise TemporalNarrativeError("Unknown or missing event review state")
        flagged = bool(set(supporting) & affected_periods)
        if (review_state == "pending_data_quality") != flagged:
            raise TemporalNarrativeError("Data-quality review state conflicts with flags")
        if review_state == "pending_data_quality" and "POL-005" not in sources:
            raise TemporalNarrativeError("POL-005 attribution is unavailable")
        recognized[key] = AttributedDeclineEvent(
            event_period=period,
            evidence_periods=tuple(supporting),
            review_state=review_state,
        )

    if len(recognized) != len(observed_by_key):
        raise TemporalNarrativeError("Not all observed events have policy-event evidence")
    return tuple(recognized[k] for k in sorted(recognized))


def compose_verified_decline_event_narrative(
    audit: dict, tool_evidence: list[dict], *,
    account_id: int, as_of_period: str, market: int,
) -> str:
    """Render every verified three-decline event, with its POL-002 review state.

    This reports *only* the validated decline-event-count claim and the matched
    policy event metadata. It does not assert current warning registration,
    an ongoing declining trend, referrals, other criteria, or risk actions.
    """
    events = attribute_verified_decline_events(
        audit, tool_evidence, account_id=account_id,
        as_of_period=as_of_period, market=market,
    )
    policy_evidence = audit["policy"]["checks"][0]["audit"]["evidence"]
    start, end = policy_evidence["window_start"], policy_evidence["window_end"]

    sentences = [
        f"For account {account_id} in market {market}, the POL-002 evaluation "
        f"window as of {as_of_period} runs from {start} to {end}."
    ]
    for event in events:
        first = event.evidence_periods[0]
        observation = (
            "Three consecutive monthly score declines were observed "
            f"from the baseline on {first} through {event.event_period}. "
        )
        if event.review_state == "eligible":
            interpretation = (
                "The policy evaluator classified this POL-002 decline event "
                "as eligible for early-warning consideration."
            )
        else:
            interpretation = (
                "The policy evaluator classified this POL-002 decline event "
                "as pending data-quality review under POL-005; it is not yet "
                "eligible for early-warning escalation."
            )
        sentences.append(observation + interpretation)

    sentences.append(
        "These statements do not establish whether an operational warning "
        "was registered or cleared, nor whether the decline pattern is ongoing "
        "as of the evaluation date."
    )
    return "\n\n".join(sentences)
