"""Module 5.3.2: policy applicability, data-quality holds, and decisions.

Pure functions: no LLM, no database, no policy text hardcoded into prompts.
Policies consulted: POL-002, POL-004, POL-005, POL-007.
Result = computed signals and review INDICATIONS, never official case status.
"""

from collections import defaultdict
from dataclasses import asdict
from math import ceil

from src.temporal_policy import (
    STANDARD_THRESHOLDS, WarningThresholds, _month,
    evaluate_threshold_signals, normalize_history,
)


def _policy_for_market(market: int, as_of: str,
                       site_onboarded_period: str | None,
                       last_exception_review_period: str | None) -> dict:
    """Market 1 is standard; other markets require explicit onboarding metadata.

    Here 'onboarded within the last 12 months' means <= 11 elapsed calendar
    months, inclusive of the onboarding month. The site review date is separate
    and is never inferred from the score history.
    """
    if market == 1:
        return {"mode": "standard", "reason": "POL-004: Site 1",
                "quarterly_exception_review": "not_applicable",
                "thresholds": STANDARD_THRESHOLDS}
    if site_onboarded_period is None:
        raise ValueError("site_onboarded_period is required for markets other than 1")

    age = _month(as_of) - _month(site_onboarded_period)
    if age < 0:
        raise ValueError("Site onboarding date cannot be after as_of_period")
    if age >= 12:
        return {"mode": "standard", "reason": "Site no longer within first 12 months",
                "quarterly_exception_review": "not_applicable",
                "thresholds": STANDARD_THRESHOLDS}

    # POL-004: relax numeric trigger thresholds by 25%. An integer event
    # requirement becomes the smallest integer >= 1.25 * standard value.
    thresholds = WarningThresholds(
        score_drop=ceil(STANDARD_THRESHOLDS.score_drop * 1.25),
        bin_drop=ceil(STANDARD_THRESHOLDS.bin_drop * 1.25),
        consecutive_declines=ceil(STANDARD_THRESHOLDS.consecutive_declines * 1.25),
        volatility=STANDARD_THRESHOLDS.volatility * 1.25,
    )
    if last_exception_review_period is None:
        review = "unverified"
    else:
        review_age = _month(as_of) - _month(last_exception_review_period)
        if review_age < 0:
            raise ValueError("Review date cannot be after as_of_period")
        review = "current" if review_age < 3 else "review_due"
    return {"mode": "grace", "reason": "POL-004: onboarded within last 12 months",
            "quarterly_exception_review": review, "thresholds": thresholds}


def _data_quality_flags(rows: list[dict], window_start_month: int) -> list[dict]:
    """Flag >150-point changes and EXACT reversals within 1-2 periods.

    POL-005's 'near-equal' has no defined tolerance. Rather than invent one,
    the code only automatically recognizes exact reversals; other similar
    movements require manual investigation. A flag is NOT a confirmed error.
    """
    flags = []
    for i in range(1, len(rows)):
        prev, curr = rows[i-1], rows[i]
        if curr["month"] == prev["month"] + 1:
            delta = curr["score"] - prev["score"]
            if abs(delta) > 150 and curr["month"] >= window_start_month - 5:
                flags.append({
                    "kind": "single_change_over_150",
                    "period": curr["period"], "score_change": delta,
                    "affected_periods": [prev["period"], curr["period"]],
                    "policy": "POL-005",
                })

    for pivot_i in range(1, len(rows)):
        base, pivot = rows[pivot_i-1], rows[pivot_i]
        if pivot["month"] != base["month"] + 1 or pivot["score"] == base["score"]:
            continue
        for delay in (1, 2):
            end_i = pivot_i + delay
            if end_i >= len(rows):
                continue
            section = rows[pivot_i-1:end_i+1]
            if (all(b["month"] == a["month"] + 1
                    for a, b in zip(section, section[1:]))
                    and section[-1]["score"] == base["score"]
                    and section[-1]["month"] >= window_start_month - 5):
                flags.append({
                    "kind": "exact_reversal_candidate",
                    "period": section[-1]["period"],
                    "affected_periods": [r["period"] for r in section],
                    "policy": "POL-005",
                })
                break
    return flags


def _indication(eligible: bool, any_raw: bool, complete: bool) -> str:
    if eligible:
        return "indicated"
    if any_raw:
        return "pending_data_quality"
    return "not_indicated" if complete else "insufficient_evidence"


def evaluate_policy_decision(
    history: list[dict], as_of_period: str,
    *, site_onboarded_period: str | None = None,
    last_exception_review_period: str | None = None,
) -> dict:
    """Compute review indications under POL-002/004/005/007 as of one month.

    - Two distinct warning criteria anywhere in 3 months => analyst referral.
    - Two distinct criteria in ONE month, OR a 3+ bin drop => committee.
    - Data-quality-affected evidence => review is PENDING, not cleared.
    - Committee site-wide aggregate rule requires another tool (not assessed).
    """
    rows = normalize_history(history, as_of_period)
    markets = {r.get("market") for r in history if _month(r["period"]) <= _month(as_of_period)}
    if len(markets) != 1 or None in markets:
        raise ValueError("All account observations need one consistent market")
    market = markets.pop()
    if not isinstance(market, int) or isinstance(market, bool):
        raise ValueError("market must be an integer")

    applicability = _policy_for_market(
        market, as_of_period, site_onboarded_period, last_exception_review_period,
    )
    threshold_result = evaluate_threshold_signals(
        history, as_of_period, applicability["thresholds"]
    )
    complete = threshold_result["coverage_complete"]
    window_start_month = _month(threshold_result["window_start"])
    flags = _data_quality_flags(rows, window_start_month)
    suspect_months = {p for flag in flags for p in flag["affected_periods"]}

    events = []
    for criterion, hits in threshold_result["events"].items():
        for hit in hits:
            event = {"criterion": criterion, **hit}
            event["review_state"] = (
                "pending_data_quality" if suspect_months.intersection(hit["evidence_periods"])
                else "eligible"
            )
            events.append(event)
    events.sort(key=lambda e: (e["period"], e["criterion"]))

    eligible = [e for e in events if e["review_state"] == "eligible"]
    all_types = {e["criterion"] for e in events}
    eligible_types = {e["criterion"] for e in eligible}

    eligible_by_month = defaultdict(set)
    raw_by_month = defaultdict(set)
    for event in events:
        raw_by_month[event["period"]].add(event["criterion"])
        if event["review_state"] == "eligible":
            eligible_by_month[event["period"]].add(event["criterion"])

    # POL-007's unconditional 3+ downward-bin migration is separate from
    # POL-004's adjusted 2-bin trigger threshold and is never relaxed.
    severe = []
    for prev, curr in zip(rows, rows[1:]):
        if (curr["month"] >= window_start_month
                and curr["month"] == prev["month"] + 1
                and curr["bin"] - prev["bin"] <= -3):
            severe.append({
                "period": curr["period"], "bin_change": curr["bin"] - prev["bin"],
                "evidence_periods": [prev["period"], curr["period"]],
                "review_state": (
                    "pending_data_quality" if suspect_months.intersection((prev["period"], curr["period"]))
                    else "eligible"
                ),
            })

    eligible_committee = (any(len(types) >= 2 for types in eligible_by_month.values())
                          or any(s["review_state"] == "eligible" for s in severe))
    raw_committee = (any(len(types) >= 2 for types in raw_by_month.values())
                     or bool(severe))
    active_flags = [
        f for f in flags
        if (any(_month(p) >= window_start_month for p in f["affected_periods"])
            or any(set(f["affected_periods"]) & set(e["evidence_periods"]) for e in events))
    ]
    warning_status = (
        "triggered" if eligible else
        "pending_data_quality" if events else
        "not_triggered" if complete else "insufficient_evidence"
    )
    review_status = applicability["quarterly_exception_review"]
    return {
        "as_of_period": threshold_result["as_of_period"],
        "window_start": threshold_result["window_start"],
        "window_end": threshold_result["window_end"],
        "market": market,
        "policy_applicability": {
            "mode": applicability["mode"], "reason": applicability["reason"],
            "quarterly_exception_review": review_status,
            "thresholds": asdict(applicability["thresholds"]),
        },
        "events": events,
        "data_quality_flags": active_flags,
        "severe_downward_migrations": severe,
        "warning_signal_status": warning_status,
        "analyst_referral": _indication(len(eligible_types) >= 2, len(all_types) >= 2, complete),
        "analyst_referral_deadline_rule": "within 5 business days (POL-002)",
        "committee_review_account_rules": _indication(
            eligible_committee, raw_committee, complete
        ),
        "committee_site_aggregate_rule": "not_evaluated",
        "data_quality_review": "required" if active_flags else "not_flagged",
        "risk_action_gate": (
            "hold_pending_data_quality" if active_flags else
            "verify_site_exception" if applicability["mode"] == "grace" and review_status != "current"
            else "requires_human_authorization"
        ),
        "coverage_complete": complete,
        "missing_baseline_months": threshold_result["missing_baseline_months"],
        "administrative_warning_status": "not_determined",
        "manual_policy_review_required": (
            applicability["mode"] == "grace" and review_status != "current"
        ),
        "policy_sources": ["POL-002", "POL-004", "POL-005", "POL-007"],
    }
