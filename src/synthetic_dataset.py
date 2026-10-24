"""Fully invented scenario generator. Standard library only; no old-data inputs.

Write a NEW output directory containing CSV + scenario and provenance sidecars.
No database loading, policy decisions, model requests or system-clock inputs.
"""

import argparse
from collections import Counter
import csv
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .synthetic_foundations import (
    ANCHORS, ANCHOR_VERSION, MAPPING_VERSION, ScoreRow,
    build_anchor, monthly_calendar, score_to_bin,
)

GENERATOR_VERSION = "synthetic-generator-1"
STREAM_VERSION = "sha256-counter-modulo-1"
CSV_FIELDS = ("account_id", "period", "score", "bin", "market")
OUTPUT_NAMES = ("scores_extended.csv", "scenarios.json", "manifest.json")
BACKGROUND_FAMILIES = (
    "stable", "improving", "declining", "sudden_drop",
    "recovery", "volatile", "data_quality", "severe_drop",
)
# These independent shifts preserve the promised factual tail patterns.
SHIFT_BOUNDS = {
    "stable": (-20, 0), "improving": (-10, 10), "declining": (-8, 8),
    "sudden_drop": (-10, 10), "recovery": (-10, 10),
    "volatile": (-15, 15), "data_quality": (0, 10), "severe_drop": (0, 8),
}


@dataclass(frozen=True)
class DatasetConfig:
    seed: int = 1729
    accounts_per_market: int = 24
    start_period: str = "1990-01-01"
    months: int = 24
    new_market_months: int = 12

    def __post_init__(self):
        if type(self.seed) is not int or not 0 <= self.seed <= 2**63 - 1:
            raise ValueError("seed must be an integer in 0..2**63-1, not bool")
        if (type(self.accounts_per_market) is not int
                or not len(ANCHORS) <= self.accounts_per_market <= 999_999):
            raise ValueError("accounts_per_market must be an integer in 11..999999")
        calendar = monthly_calendar(self.start_period, self.months)
        if (type(self.new_market_months) is not int
                or not 8 <= self.new_market_months <= 12
                or self.new_market_months > self.months or self.months < 12):
            raise ValueError("require months >= 12 and 8 <= new_market_months <= min(12, months)")
        # Reserve one fictional month before the window for established-site onboarding.
        if self.start_period == "0001-01-01":
            raise ValueError("calendar needs a representable earlier onboarding month")
        if date.fromisoformat(calendar[-1]).year < 2:
            raise ValueError("as-of year must be at least 0002 for scanner compatibility")


def profile_config(profile="small", **overrides) -> DatasetConfig:
    if profile not in ("small", "large"):
        raise ValueError("profile must be small or large")
    values = {} if profile == "small" else {
        "accounts_per_market": 1000, "start_period": "1987-01-01", "months": 60,
    }
    values.update(overrides)
    return DatasetConfig(**values)


def _canonical_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class AccountStream:
    """Versioned deterministic variation, independent of account iteration order.

    Digest modulo range is sufficient for invented demonstration jitter; it is
    not a claim of statistical calibration or cryptographic random sampling.
    """
    def __init__(self, seed: int, market: int, ordinal: int):
        self.key = [STREAM_VERSION, GENERATOR_VERSION, seed, market, ordinal]
        self.counter = 0

    def integer(self, low: int, high: int) -> int:
        payload = json.dumps(self.key + [self.counter], separators=(",", ":"))
        self.counter += 1
        value = int.from_bytes(hashlib.sha256(payload.encode("ascii")).digest(), "big")
        return low + value % (high - low + 1)


def market_calendar(config: DatasetConfig, market: int) -> tuple[str, ...]:
    if type(market) is not int or market not in (1, 2, 3):
        raise ValueError("market must be 1, 2 or 3")
    calendar = monthly_calendar(config.start_period, config.months)
    return calendar[-config.new_market_months:] if market == 3 else calendar


def _assert_background_pattern(family: str, scores: tuple[int, ...]):
    """Factual construction invariants only, never a competing policy engine."""
    bins = tuple(score_to_bin(s) for s in scores)
    valid = {
        "stable": len(set(bins)) == 1 and max(scores) - min(scores) <= 4,
        "improving": all(b > a for a, b in zip(scores, scores[1:])),
        "declining": all(0 < a - b < 40 for a, b in zip(scores, scores[1:])),
        "sudden_drop": scores[2] - scores[3] == 100 and bins[2] - bins[3] == 2,
        "recovery": bins[2] == 3 and bins[3:] == (4, 4, 4),
        "volatile": scores[0] == scores[2] == scores[4]
                    and scores[1] == scores[3] == scores[5]
                    and scores[1] - scores[0] == 80,
        "data_quality": scores[2] - scores[1] == 190 and scores[1] == scores[3],
        "severe_drop": scores[2] - scores[3] == 120 and bins[2] - bins[3] == 3,
    }[family]
    if not valid:
        raise ValueError(f"generated {family} tail violated its factual contract")


def build_account(config: DatasetConfig, market: int, ordinal: int):
    """Return this account's rows and metadata; no dependency on other accounts."""
    if type(ordinal) is not int or not 1 <= ordinal <= config.accounts_per_market:
        raise ValueError("ordinal outside configured account range")
    periods = market_calendar(config, market)
    definitions = tuple(ANCHORS.items())
    if ordinal <= len(definitions):
        family, definition = definitions[ordinal - 1]
        rows = build_anchor(family, market=market, start_period=periods[0], months=len(periods))
        kind, shift = "anchor", 0
        coverage = definition.coverage
    else:
        # Equal-weight round robin, stable on increasing account count.
        family = BACKGROUND_FAMILIES[(ordinal - len(definitions) - 1) % len(BACKGROUND_FAMILIES)]
        stream = AccountStream(config.seed, market, ordinal)
        shift = stream.integer(*SHIFT_BOUNDS[family])
        tail = tuple(s + shift for s in ANCHORS[family].tail)
        _assert_background_pattern(family, tail)
        # Additional seeded variety before the exact six-month event segment.
        prefix = tuple(tail[0] + stream.integer(-2, 2) for _ in periods[:-6])
        scores = prefix + tail
        rows = tuple(ScoreRow(market * 1_000_000 + ordinal, period, s, score_to_bin(s), market)
                     for period, s in zip(periods, scores))
        kind, coverage = "background", "complete"
    observed = {r.period for r in rows}
    metadata = {
        "account_id": rows[0].account_id, "market": market, "ordinal": ordinal,
        "kind": kind, "scenario_id": family, "coverage": coverage,
        "scheduled_months": len(periods), "observed_months": len(rows),
        "omitted_periods": [p for p in periods if p not in observed],
        "first_observed_period": rows[0].period, "last_observed_period": rows[-1].period,
        "tail_periods": list(periods[-6:]), "score_shift": shift,
        "description": ANCHORS[family].description,
    }
    return rows, metadata


def _previous_month(period: str) -> str:
    d = date.fromisoformat(period)
    if d.month == 1:
        return f"{d.year - 1:04d}-12-01"
    return f"{d.year:04d}-{d.month - 1:02d}-01"


def _write_staging(config: DatasetConfig, staging: Path) -> dict:
    """Stream CSV account by account; sidecar memory is O(account count)."""
    scenario_accounts = []
    market_counts = {}
    previous_key = None
    total_rows = 0
    csv_path = staging / OUTPUT_NAMES[0]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\n")
        writer.writerow(CSV_FIELDS)
        for market in (1, 2, 3):
            count = 0
            for ordinal in range(1, config.accounts_per_market + 1):
                rows, metadata = build_account(config, market, ordinal)
                scenario_accounts.append(metadata)
                for row in rows:
                    key = (row.market, row.account_id, row.period)
                    if previous_key is not None and key <= previous_key:
                        raise ValueError("rows must have unique keys and canonical order")
                    if row.bin != score_to_bin(row.score):
                        raise ValueError("score/bin inconsistency")
                    previous_key = key
                    year, month, day = row.period.split("-")
                    writer.writerow((row.account_id, f"{day}.{month}.{year}", row.score,
                                     row.bin, row.market))
                    count += 1
            market_counts[str(market)] = {"accounts": config.accounts_per_market, "rows": count}
            total_rows += count
    scenarios = {
        "schema_version": 1, "anchor_version": ANCHOR_VERSION,
        "meaning": "Construction metadata only; not policy outcomes or privileged agent evidence.",
        "background_allocation": "Equal-weight round robin over eight families after 11 fixed anchors.",
        "accounts": scenario_accounts,
    }
    (staging / OUTPUT_NAMES[1]).write_bytes(_canonical_bytes(scenarios))
    calendar = market_calendar(config, 1)
    new_calendar = market_calendar(config, 3)
    scheduled = config.accounts_per_market * (2 * config.months + config.new_market_months)
    expected_omissions = 2 * config.months + config.new_market_months
    if total_rows != scheduled - expected_omissions:
        raise ValueError("row count disagrees with explicit coverage omissions")
    source_hashes = {name: _sha_file(Path(__file__).parent / name)
                     for name in ("synthetic_foundations.py", "synthetic_dataset.py")}
    manifest = {
        "schema_version": 1, "generator_version": GENERATOR_VERSION,
        "mapping_version": MAPPING_VERSION, "anchor_version": ANCHOR_VERSION,
        "stream_version": STREAM_VERSION, "configuration": asdict(config),
        "as_of_period": calendar[-1], "score_range_inclusive": [0, 449],
        "bin_rule": "1 + score // 50", "markets": market_counts,
        "accounts": 3 * config.accounts_per_market, "rows": total_rows,
        "scheduled_rows": scheduled, "omitted_rows": scheduled - total_rows,
        "scenario_counts": dict(sorted(Counter(a["scenario_id"] for a in scenario_accounts).items())),
        "kind_counts": dict(sorted(Counter(a["kind"] for a in scenario_accounts).items())),
        "market_metadata": {
            "1": {"site_onboarded_period": _previous_month(calendar[0]),
                  "last_exception_review_period": None},
            "2": {"site_onboarded_period": _previous_month(calendar[0]),
                  "last_exception_review_period": None},
            "3": {"site_onboarded_period": new_calendar[0],
                  "last_exception_review_period": calendar[-3]},
        },
        "provenance": "Independently authored fictional scenarios; no workplace-data inputs or fitted parameters.",
        "policy_provenance": "Owner confirmed all demonstration policy memos and thresholds independently invented (2026-10-23).",
        "policy_scope": "No policy corpus loaded or changed; v1.0/v1.1 remain separate. No policy outcomes asserted.",
        "source_sha256": source_hashes,
        "files_sha256": {name: _sha_file(staging / name) for name in OUTPUT_NAMES[:2]},
    }
    (staging / OUTPUT_NAMES[2]).write_bytes(_canonical_bytes(manifest))
    return manifest


def generate_dataset(config: DatasetConfig, output_directory: Path | str) -> dict:
    """Publish validated files to a new directory; refuse any existing target.

    Stage all bytes first, reserve the target exclusively, publish manifest last.
    On an ordinary write error remove only files created by this operation.
    A process crash can leave an incomplete target: absence of manifest means
    it is not a completed dataset. Existing targets are never overwritten.
    """
    if not isinstance(config, DatasetConfig):
        raise TypeError("config must be a DatasetConfig")
    output = Path(output_directory).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".synthetic-staging-", dir=output.parent) as temporary:
        staging = Path(temporary)
        manifest = _write_staging(config, staging)
        output.mkdir()  # Exclusive reservation also rejects a concurrently created target.
        created = []
        try:
            for name in OUTPUT_NAMES:
                destination = output / name
                os.replace(staging / name, destination)
                created.append(destination)
        except BaseException:
            for path in reversed(created):
                path.unlink()
            output.rmdir()
            raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("small", "large"), default="small")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--accounts-per-market", type=int)
    parser.add_argument("--start-period")
    parser.add_argument("--months", type=int)
    parser.add_argument("--new-market-months", type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    values = {key: value for key, value in vars(args).items()
              if key not in ("profile", "output_dir") and value is not None}
    try:
        config = profile_config(args.profile, **values)
        manifest = generate_dataset(config, args.output_dir)
    except (ValueError, TypeError, OSError) as exc:
        parser.exit(2, f"Generation failed: {exc}\n")
    print(json.dumps({"accounts": manifest["accounts"], "rows": manifest["rows"],
                      "omitted_rows": manifest["omitted_rows"],
                      "as_of_period": manifest["as_of_period"],
                      "csv_sha256": manifest["files_sha256"][OUTPUT_NAMES[0]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
