"""Independent fictional score rules and fixed anchors; standard library only.

Pure constructors: no file access, clock, randomness, policy evaluation or APIs.
Scenario labels describe construction, not approved risk decisions.
"""

from dataclasses import asdict, dataclass
from datetime import date
from types import MappingProxyType

MAPPING_VERSION = "demo-bins-1"
ANCHOR_VERSION = "synthetic-anchors-1"
DEFAULT_AS_OF = "1991-12-01"
MIN_SCORE = 0
MAX_SCORE = 449
BIN_WIDTH = 50


def score_to_bin(score: int) -> int:
    """Nine inclusive 50-point bins: 0..49 -> 1, ..., 400..449 -> 9."""
    if type(score) is not int:
        raise TypeError("score must be a Python integer (not bool)")
    if not MIN_SCORE <= score <= MAX_SCORE:
        raise ValueError("score must be in 0..449 demonstration units")
    return 1 + score // BIN_WIDTH


def monthly_calendar(start_period: str, months: int) -> tuple[str, ...]:
    """Construct consecutive ISO month starts directly, without source dates.

    At least one month is required; dates must be canonical YYYY-MM-01.
    Reject an overflowing calendar before allocating it.
    """
    if type(start_period) is not str:
        raise TypeError("start_period must be a YYYY-MM-01 string")
    try:
        start = date.fromisoformat(start_period)
    except ValueError as exc:
        raise ValueError("start_period must be a YYYY-MM-01 string") from exc
    if start.day != 1 or start.isoformat() != start_period:
        raise ValueError("start_period must be a canonical YYYY-MM-01")
    if type(months) is not int:
        raise TypeError("months must be a Python integer (not bool)")
    if months < 1:
        raise ValueError("months must be positive")
    first = (start.year - 1) * 12 + start.month - 1
    if months > 9999 * 12 - first:
        raise ValueError("calendar extends beyond year 9999")
    return tuple(
        f"{index // 12 + 1:04d}-{index % 12 + 1:02d}-01"
        for index in range(first, first + months)
    )


@dataclass(frozen=True)
class AnchorDefinition:
    ordinal: int
    tail: tuple[int, ...]
    description: str
    coverage: str = "complete"


# Stable ordinals are part of the fixture contract; append, do not renumber.
ANCHORS = MappingProxyType({
    "stable": AnchorDefinition(1, (345, 348, 346, 349, 347, 348),
                               "Small fluctuations within bin 7."),
    "improving": AnchorDefinition(2, (162, 180, 198, 216, 234, 252),
                                  "Steady improvement across bin boundaries."),
    "declining": AnchorDefinition(3, (325, 313, 301, 289, 277, 265),
                                  "Consecutive declines without a 40-point loss."),
    "sudden_drop": AnchorDefinition(4, (329, 329, 329, 229, 229, 229),
                                    "Persistent two-bin, 100-point drop."),
    "recovery": AnchorDefinition(5, (125, 125, 125, 175, 175, 175),
                                 "Watch-to-Standard pattern; no eligibility claim."),
    "volatile": AnchorDefinition(6, (225, 305, 225, 305, 225, 305),
                                 "Population stdev 40; exact reversals also occur."),
    "data_quality": AnchorDefinition(7, (200, 200, 390, 200, 200, 200),
                                     "190-point jump and exact reversal candidate."),
    "severe_drop": AnchorDefinition(8, (359, 359, 359, 239, 239, 239),
                                    "Persistent three-bin drop below 151 points."),
    "missing_interior": AnchorDefinition(9, (345,) * 6,
                                         "Omit calendar month t-2.", "missing_interior"),
    "missing_latest": AnchorDefinition(10, (345,) * 6,
                                       "Omit calendar month t.", "missing_latest"),
    "short_history": AnchorDefinition(11, (345,) * 6,
                                      "Retain only months t-1 and t.", "short_history"),
})


@dataclass(frozen=True)
class ScoreRow:
    account_id: int
    period: str
    score: int
    bin: int
    market: int

    def as_dict(self) -> dict:
        """Five database columns only; scenario metadata stays separate."""
        return asdict(self)


def anchor_account_id(scenario_id: str, market: int = 1) -> int:
    if type(scenario_id) is not str:
        raise TypeError("scenario_id must be a string")
    if scenario_id not in ANCHORS:
        raise ValueError(f"unknown scenario_id: {scenario_id!r}")
    if type(market) is not int:
        raise TypeError("market must be a Python integer (not bool)")
    if market not in (1, 2, 3):
        raise ValueError("fixed anchors support demonstration markets 1, 2 and 3")
    return market * 1_000_000 + ANCHORS[scenario_id].ordinal


def build_anchor(
    scenario_id: str, *, market: int = 1,
    start_period: str = "1990-01-01", months: int = 24,
) -> tuple[ScoreRow, ...]:
    """Pad an exact six-month tail; then apply explicit coverage omissions.

    Require eight scheduled months even for coverage cases, so shortened
    observations are deliberate and never accidental calendar truncation.
    Event tails move with the chosen end month; IDs do not.
    """
    account_id = anchor_account_id(scenario_id, market)
    periods = monthly_calendar(start_period, months)
    if months < 8:
        raise ValueError("anchor calendar must contain at least eight months")
    spec = ANCHORS[scenario_id]
    scores = (spec.tail[0],) * (months - len(spec.tail)) + spec.tail
    omitted = {
        "complete": (),
        "missing_interior": (months - 3,),
        "missing_latest": (months - 1,),
        "short_history": tuple(range(months - 2)),
    }[spec.coverage]
    return tuple(
        ScoreRow(account_id, period, score, score_to_bin(score), market)
        for index, (period, score) in enumerate(zip(periods, scores))
        if index not in omitted
    )


def build_default_anchors() -> tuple[ScoreRow, ...]:
    """33 fixed accounts / 600 rows; not the future 72-account default dataset."""
    rows = [
        row
        for market in (1, 2, 3)
        for scenario_id in ANCHORS
        for row in build_anchor(
            scenario_id, market=market,
            start_period="1991-01-01" if market == 3 else "1990-01-01",
            months=12 if market == 3 else 24,
        )
    ]
    return tuple(sorted(rows, key=lambda row: (row.market, row.account_id, row.period)))


if __name__ == "__main__":
    import json

    rows = build_default_anchors()
    print(json.dumps({
        "mapping_version": MAPPING_VERSION,
        "anchor_version": ANCHOR_VERSION,
        "scope": "fixed anchors only; not the complete replacement dataset",
        "as_of_period": DEFAULT_AS_OF,
        "accounts": len({row.account_id for row in rows}),
        "rows": len(rows),
        "markets": {
            str(market): {
                "accounts": len({r.account_id for r in rows if r.market == market}),
                "rows": sum(r.market == market for r in rows),
            } for market in (1, 2, 3)
        },
    }, indent=2))
