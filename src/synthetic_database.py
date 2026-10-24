"""Verify a generated dataset, then build a separate new SQLite output bundle.

The original application/database are never imported or rebuilt by this module.
"""

import argparse
from contextlib import closing
import csv
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory

from .synthetic_dataset import DatasetConfig, OUTPUT_NAMES, generate_dataset
from .synthetic_foundations import score_to_bin

SCHEMA = """
CREATE TABLE scores (
    account_id INTEGER NOT NULL,
    period TEXT NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 449),
    bin INTEGER NOT NULL CHECK (bin BETWEEN 1 AND 9 AND bin = 1 + score / 50),
    market INTEGER NOT NULL CHECK (market IN (1, 2, 3)),
    PRIMARY KEY (account_id, period)
);
CREATE INDEX idx_scores_market ON scores(market);
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _csv_rows(path: Path):
    """Validate physical row types/date format independently of generation."""
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        if reader.fieldnames != ["account_id", "period", "score", "bin", "market"]:
            raise ValueError("unexpected CSV columns")
        previous = None
        for raw in reader:
            if set(raw) != set(reader.fieldnames):
                raise ValueError("unexpected CSV fields")
            values = [int(raw[name]) for name in ("account_id", "score", "bin", "market")]
            if any(str(value) != raw[name] for name, value in
                   zip(("account_id", "score", "bin", "market"), values)):
                raise ValueError("noncanonical integer in CSV")
            account, score, bin_number, market = values
            parts = raw["period"].split(".")
            if len(parts) != 3:
                raise ValueError("expected DD.MM.YYYY CSV period")
            day, month, year = map(int, parts)
            parsed = date(year, month, day)
            if day != 1 or raw["period"] != f"{day:02d}.{month:02d}.{year:04d}":
                raise ValueError("expected a canonical monthly CSV period")
            key = (market, account, parsed.isoformat())
            if previous is not None and key <= previous:
                raise ValueError("duplicate or unsorted CSV history")
            if (account <= 0 or market not in (1, 2, 3)
                    or bin_number != score_to_bin(score)):
                raise ValueError("invalid CSV score/bin or identity")
            previous = key
            yield account, parsed.isoformat(), score, bin_number, market


def build_database(dataset_directory: Path | str, output_directory: Path | str) -> dict:
    """Require exact regeneration of the three input files before DB loading.

    Verify a private snapshot so input edits during loading cannot change rows.
    Publish a NEW output directory, with receipt last; never replace prior runs.
    """
    dataset = Path(dataset_directory).resolve()
    output = Path(output_directory).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".synthetic-db-", dir=output.parent) as temporary:
        work = Path(temporary)
        snapshot = work / "snapshot"
        snapshot.mkdir()
        for name in OUTPUT_NAMES:
            source = dataset / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"missing or nonregular dataset file: {name}")
            shutil.copyfile(source, snapshot / name)
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        if type(manifest) is not dict or type(manifest.get("configuration")) is not dict:
            raise ValueError("manifest configuration is missing")
        config = DatasetConfig(**manifest["configuration"])
        generated = work / "regenerated"
        generate_dataset(config, generated)
        for name in OUTPUT_NAMES:
            if sha256(snapshot / name) != sha256(generated / name):
                raise ValueError(f"dataset does not match recorded generator/configuration: {name}")

        db = work / "scores.db"
        with closing(sqlite3.connect(db)) as conn:
            with conn:
                conn.executescript(SCHEMA)
                conn.executemany("INSERT INTO scores VALUES (?, ?, ?, ?, ?)",
                                 _csv_rows(snapshot / "scores_extended.csv"))
            rows, accounts, first, last = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT account_id), MIN(period), MAX(period) FROM scores"
            ).fetchone()
            if rows != manifest["rows"] or accounts != manifest["accounts"]:
                raise ValueError("database counts disagree with verified manifest")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            markets = {
                str(market): {"rows": count, "accounts": account_count}
                for market, count, account_count in conn.execute(
                    "SELECT market, COUNT(*), COUNT(DISTINCT account_id) FROM scores GROUP BY market")
            }
            if markets != manifest["markets"]:
                raise ValueError("database market counts disagree with manifest")
        receipt = {
            "schema_version": 1, "loader_version": "synthetic-db-1",
            "verification": "all three input files exactly reproduced from configuration and current generator source",
            "rows": rows, "accounts": accounts, "first_period": first, "last_period": last,
            "as_of_period": manifest["as_of_period"], "markets": markets,
            "input_sha256": {name: sha256(snapshot / name) for name in OUTPUT_NAMES},
            "database_sha256": sha256(db),
            "loader_source_sha256": sha256(Path(__file__)),
            "scope": "isolated synthetic database only; no application migration or publication approval",
        }
        (work / "database_receipt.json").write_text(
            json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
        output.mkdir()
        created = []
        try:
            for name in ("scores.db", "database_receipt.json"):
                destination = output / name
                os.replace(work / name, destination)
                created.append(destination)
        except BaseException:
            for path in reversed(created):
                path.unlink()
            output.rmdir()
            raise
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = build_database(args.dataset_dir, args.output_dir)
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"Database build failed: {exc}\n")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
