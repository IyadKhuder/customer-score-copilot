"""Module 5.5.6.1: observed score events from recorded get_history evidence.

This module deliberately does NOT apply policy rules, create VerifiedFinding
objects, or authorize publication. That comes after the policy validator's
existing field-level contract has been inspected.
"""

from dataclasses import dataclass
from datetime import date


class TemporalEvidenceError(ValueError):
    """Recorded history does not safely support temporal-event detection."""


@dataclass(frozen=True)
class ObservedTemporalEvent:
    """Observed consecutive declines, not a warning or policy decision."""

    kind: str
    account_id: int
    market: int
    as_of_period: str
    event_period: str
    evidence_periods: tuple[str, ...]
    scores: tuple[int, ...]
    score_changes: tuple[int, ...]


def _iso_date(value: object) -> str:
    if not isinstance(value, str):
        raise TemporalEvidenceError("Period must be an ISO date string")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Non-canonical ISO date")
    except ValueError as exc:
        raise TemporalEvidenceError("Period must be a valid YYYY-MM-DD date") from exc
    return value


def _month_index(period: str) -> int:
    """Consecutive calendar months differ by exactly one in this index."""
    return int(period[:4]) * 12 + int(period[5:7])


def _history_for_account(tool_evidence: list[dict], account_id: int,
                         market: int) -> list[dict]:
    if not isinstance(tool_evidence, list):
        raise TemporalEvidenceError("Tool evidence must be a list")

    histories = []
    for call in tool_evidence:
        if not isinstance(call, dict) or call.get("tool_name") != "get_history":
            continue
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            raise TemporalEvidenceError("Invalid get_history arguments")
        if type(arguments.get("account_id")) is not int:
            raise TemporalEvidenceError("Invalid get_history account ID")
        if arguments["account_id"] != account_id:
            continue
        rows = call.get("result")
        if not isinstance(rows, list) or not rows:
            raise TemporalEvidenceError("No recorded history for requested account")
        validated = []
        seen_months = set()
        for row in rows:
            if (not isinstance(row, dict)
                or type(row.get("score")) is not int
                or type(row.get("bin")) is not int
                or type(row.get("market")) is not int
                or row["market"] != market):
                raise TemporalEvidenceError("Invalid or inconsistent history row")
            period = _iso_date(row.get("period"))
            month = _month_index(period)
            if month in seen_months:
                raise TemporalEvidenceError("Multiple observations within one month")
            seen_months.add(month)
            validated.append({"period": period, "score": row["score"],
                              "bin": row["bin"], "market": row["market"]})
        histories.append(sorted(validated, key=lambda x: x["period"]))

    if not histories:
        raise TemporalEvidenceError("Matching get_history tool evidence is missing")
    if any(history != histories[0] for history in histories[1:]):
        raise TemporalEvidenceError("Conflicting recorded histories")
    return histories[0]


def detect_consecutive_declines(
    tool_evidence: list[dict], *, account_id: int, as_of_period: str,
    market: int, transitions: int,
) -> tuple[ObservedTemporalEvent, ...]:
    """Find dated decline sequences with exactly ``transitions`` downward steps.

    A threshold of four requires FIVE score observations. This independently
    measures temporal evidence; it does not decide which policy applies.
    """
    if type(transitions) is not int or transitions < 1 or transitions > 12:
        raise TemporalEvidenceError("Invalid requested number of decline transitions")
    if type(account_id) is not int or type(market) is not int:
        raise TemporalEvidenceError("Account and market must be integer IDs")
    as_of_period = _iso_date(as_of_period)
    history = _history_for_account(tool_evidence, account_id, market)
    if not any(row["period"] == as_of_period for row in history):
        raise TemporalEvidenceError("Reporting period absent from recorded history")
    history = [row for row in history if row["period"] <= as_of_period]
    span = transitions + 1
    if len(history) < span:
        raise TemporalEvidenceError(
            "Fewer than four historical observations" if transitions == 3
            else "Insufficient history for requested decline threshold"
        )

    events = []
    for i in range(transitions, len(history)):
        window = history[i - transitions:i + 1]
        periods = tuple(row["period"] for row in window)
        scores = tuple(row["score"] for row in window)
        if not all(_month_index(b) - _month_index(a) == 1
                   for a, b in zip(periods, periods[1:])):
            continue
        if not all(b < a for a, b in zip(scores, scores[1:])):
            continue
        events.append(ObservedTemporalEvent(
            kind=f"{transitions}_consecutive_declines", account_id=account_id,
            market=market, as_of_period=as_of_period, event_period=periods[-1],
            evidence_periods=periods, scores=scores,
            score_changes=tuple(b - a for a, b in zip(scores, scores[1:])),
        ))
    return tuple(events)


def detect_three_consecutive_declines(
    tool_evidence: list[dict], *, account_id: int, as_of_period: str, market: int
) -> tuple[ObservedTemporalEvent, ...]:
    """Backward-compatible Module 5.5.6.1 detector."""
    events = detect_consecutive_declines(
        tool_evidence, account_id=account_id, as_of_period=as_of_period,
        market=market, transitions=3,
    )
    # Preserve the original kind string for existing consumers and tests.
    from dataclasses import replace
    return tuple(replace(event, kind="three_consecutive_declines") for event in events)
