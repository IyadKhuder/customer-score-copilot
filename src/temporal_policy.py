"""Module 5.3: independent, dated POL-002 signal detection.

Step 5.3.2 extracts a reusable threshold-aware engine while preserving the
public Step 5.3.1 evaluate_temporal_signals() result and its nine existing tests.
This module knows how to measure events, NOT whether risk actions are approved.
"""

from dataclasses import dataclass
from datetime import date
from statistics import pstdev


@dataclass(frozen=True)
class WarningThresholds:
    """POL-002 thresholds; a site exception may provide adjusted values."""

    score_drop: int = 40       # inclusive number of points lost
    bin_drop: int = 2          # inclusive number of bins lost
    consecutive_declines: int = 3  # number of downward *transitions*
    volatility: float = 35.0  # strict: population stdev must EXCEED this


STANDARD_THRESHOLDS = WarningThresholds()


def _month(period: str) -> int:
    parsed = date.fromisoformat(period)
    return parsed.year * 12 + parsed.month - 1


def _month_start(index: int) -> str:
    year, month_zero = divmod(index, 12)
    return f"{year:04d}-{month_zero + 1:02d}-01"


def normalize_history(history: list[dict], as_of_period: str) -> list[dict]:
    """Return sorted, monthly rows up to as-of; reject duplicates and stale data."""
    if not history:
        raise ValueError("No account history was supplied")
    as_of_month = _month(as_of_period)
    rows = sorted(
        ({"period": _month_start(_month(row["period"])),
          "month": _month(row["period"]),
          "score": int(row["score"]), "bin": int(row["bin"])}
         for row in history if _month(row["period"]) <= as_of_month),
        key=lambda row: row["month"],
    )
    if not rows or rows[-1]["month"] != as_of_month:
        raise ValueError("An observation in the as-of month is required")
    if len({row["month"] for row in rows}) != len(rows):
        raise ValueError("Duplicate account observations within a month")
    return rows


def evaluate_threshold_signals(
    history: list[dict], as_of_period: str,
    thresholds: WarningThresholds = STANDARD_THRESHOLDS,
) -> dict:
    """Detect raw warning events ending in the last 3 calendar months.

    Event evidence lists the snapshots used, allowing the next layer to mark
    events contaminated by suspect data. Older baseline months do not themselves
    make an event recent. Full negative clearance requires all 8 months.
    """
    rows = normalize_history(history, as_of_period)
    as_of_month = _month(as_of_period)
    window_start = as_of_month - 2

    observed = {r["month"] for r in rows}
    needed = set(range(as_of_month - 7, as_of_month + 1))
    missing = [_month_start(month) for month in sorted(needed - observed)]

    events = {"score_drop": [], "bin_drop": [],
              "consecutive_declines": [], "volatility": []}

    for i, current in enumerate(rows):
        if current["month"] < window_start:
            continue
        if i and current["month"] == rows[i-1]["month"] + 1:
            previous = rows[i-1]
            score_change = current["score"] - previous["score"]
            bin_change = current["bin"] - previous["bin"]
            evidence = [previous["period"], current["period"]]
            if score_change <= -thresholds.score_drop:
                events["score_drop"].append({
                    "period": current["period"], "score_change": score_change,
                    "evidence_periods": evidence,
                })
            if bin_change <= -thresholds.bin_drop:
                events["bin_drop"].append({
                    "period": current["period"], "bin_change": bin_change,
                    "evidence_periods": evidence,
                })

        length = thresholds.consecutive_declines + 1
        if i >= length - 1:
            streak = rows[i-length+1:i+1]
            if (all(b["month"] == a["month"] + 1 for a, b in zip(streak, streak[1:]))
                    and all(b["score"] < a["score"] for a, b in zip(streak, streak[1:]))):
                events["consecutive_declines"].append({
                    "period": current["period"],
                    "evidence_periods": [r["period"] for r in streak],
                })

        if i >= 5:
            last_six = rows[i-5:i+1]
            if all(b["month"] == a["month"] + 1
                   for a, b in zip(last_six, last_six[1:])):
                raw_std = pstdev(r["score"] for r in last_six)
                if raw_std > thresholds.volatility:
                    events["volatility"].append({
                        "period": current["period"], "stdev": round(raw_std, 2),
                        "evidence_periods": [r["period"] for r in last_six],
                    })

    has_signal = any(events.values())
    return {
        "as_of_period": _month_start(as_of_month),
        "window_start": _month_start(window_start),
        "window_end": _month_start(as_of_month),
        "events": events,
        "coverage_complete": not missing,
        "missing_baseline_months": missing,
        "computed_signal_status": (
            "triggered" if has_signal else
            "not_triggered" if not missing else "insufficient_evidence"
        ),
    }


def evaluate_temporal_signals(history: list[dict], as_of_period: str) -> dict:
    """Backwards-compatible Step 5.3.1 interface: STANDARD POL-002 only."""
    result = evaluate_threshold_signals(history, as_of_period)
    event_names = {
        "score_drop": "score_drop_40",
        "bin_drop": "down_2_bins",
        "consecutive_declines": "three_declines",
        "volatility": "volatility_over_35",
    }
    result["events"] = {
        old_name: [
            {k: v for k, v in event.items() if k != "evidence_periods"}
            for event in result["events"][new_name]
        ]
        for new_name, old_name in event_names.items()
    }
    result["policy_scope"] = "POL-002 standard thresholds only; provisional"
    return result
