"""Check the verified SMALL dataset using the existing tools and bounded scanner.

Explicit read-only connections prevent writes through the old score-tool module.
"""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

SMALL_CSV_SHA256 = "92059166ee2927f1bc1f35f64ae0ae009abaef864a93c8185cba8e50f1ef9cc4"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--database-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.source_root.resolve()
    bundle = args.database_dir.resolve()
    database = bundle / "scores.db"
    try:
        receipt = json.loads((bundle / "database_receipt.json").read_text(encoding="utf-8"))
        if (receipt["rows"] != 1380 or receipt["accounts"] != 72
                or receipt["input_sha256"]["scores_extended.csv"] != SMALL_CSV_SHA256
                or receipt["database_sha256"] != sha256(database)):
            raise ValueError("expected a verified default small database with matching receipt")
        for name in ("score_tools.py", "batch_scan.py", "policy_decisions.py", "temporal_policy.py"):
            if not (root / "src" / name).is_file():
                raise ValueError(f"missing source module: {name}")
    except (KeyError, ValueError, OSError, TypeError) as exc:
        parser.error(str(exc))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from src import score_tools, batch_scan

    uri = database.as_uri() + "?mode=ro"
    def readonly_connection():
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    class IntegrationTests(unittest.TestCase):
        def test_counts_and_market_account_lists(self):
            self.assertEqual(len(score_tools.list_accounts()), 72)
            for market in (1, 2, 3):
                self.assertEqual(score_tools.list_accounts(market),
                                 [market * 1_000_000 + i for i in range(1, 25)])

        def test_every_account_history_and_latest_row_match_database(self):
            with closing(readonly_connection()) as conn:
                for account in score_tools.list_accounts():
                    expected = [dict(r) for r in conn.execute(
                        "SELECT period,score,bin,market FROM scores WHERE account_id=? ORDER BY period",
                        (account,))]
                    self.assertEqual(score_tools.get_history(account), expected)
                    self.assertEqual(score_tools.get_latest_score(account), expected[-1])

        def test_severe_migration_exact_values(self):
            self.assertEqual(score_tools.get_migrations(1_000_008), [{
                "from_period": "1991-09-01", "to_period": "1991-10-01",
                "from_bin": 8, "to_bin": 5, "from_score": 359, "to_score": 239,
                "direction": "down",
            }])

        def test_trend_and_recent_volatility(self):
            decline = score_tools.compute_trend(1_000_003)
            self.assertEqual(decline["net_change"], -60)
            self.assertEqual(decline["latest_consecutive_declines"], 5)
            self.assertEqual(decline["n_periods"], 24)
            self.assertEqual(score_tools.compute_trend(1_000_006)["volatility_last_6_stdev"], 40.0)

        def test_coverage_gaps_survive_loading(self):
            interior = score_tools.get_history(1_000_009)
            self.assertEqual(len(interior), 23)
            self.assertNotIn("1991-10-01", [r["period"] for r in interior])
            self.assertEqual(score_tools.get_latest_score(1_000_010)["period"], "1991-11-01")
            self.assertEqual(len(score_tools.get_history(1_000_011)), 2)

        def test_market_average_range_and_counts(self):
            for market in (1, 2, 3):
                actual = score_tools.get_market_average(market, "1991-10-01", "1991-12-01")
                with closing(readonly_connection()) as conn:
                    expected = [{"period": row[0], "avg_score": round(row[1], 1), "n_accounts": row[2]}
                                for row in conn.execute(
                        "SELECT period,AVG(score),COUNT(*) FROM scores WHERE market=? "
                        "AND period BETWEEN '1991-10-01' AND '1991-12-01' GROUP BY period ORDER BY period",
                        (market,))]
                self.assertEqual(actual, expected)

        def test_unknown_account_and_market_are_empty(self):
            self.assertEqual(score_tools.get_history(9_999_999), [])
            self.assertIsNone(score_tools.get_latest_score(9_999_999))
            self.assertIsNone(score_tools.compute_trend(9_999_999))
            self.assertEqual(score_tools.get_market_average(99), [])

        def test_market_one_scan_has_expected_anchor_outcomes(self):
            request = dict(schema_version=1, account_ids=score_tools.list_accounts(1),
                           market=1, as_of_period="1991-12-01")
            report = batch_scan.scan_batch(request, database, source_root=root)
            self.assertEqual(report["run_status"], "completed", report.get("run_error"))
            self.assertEqual(len(report["results"]), 24)
            by_id = {r["account_id"]: r for r in report["results"]}
            for ordinal in (1, 2, 5):
                self.assertEqual(by_id[1_000_000 + ordinal]["scan_outcome"], "no_signal_in_scope")
            for ordinal in (3, 4, 6, 7, 8):
                self.assertEqual(by_id[1_000_000 + ordinal]["scan_outcome"], "attention_required")
            for ordinal in (9, 10, 11):
                self.assertEqual(by_id[1_000_000 + ordinal]["scan_outcome"], "insufficient_data")
            self.assertEqual(by_id[1_000_007]["next_action"], "data_quality_review")
            self.assertEqual(by_id[1_000_008]["next_action"], "committee_review")
            self.assertTrue(all(r["narrative"]["publishable"] is False for r in report["results"]))

        def test_scan_scope_limits_are_unchanged(self):
            for market, accounts in ((1, list(range(1, 27))), (2, [2_000_001]), (3, [3_000_001])):
                with self.subTest(market=market), self.assertRaises(batch_scan.InvalidScanRequest):
                    batch_scan.validate_request(dict(schema_version=1, account_ids=accounts,
                                                     market=market, as_of_period="1991-12-01"))

        def test_database_connection_is_read_only(self):
            with closing(score_tools._connect()) as conn:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("DELETE FROM scores")

    for name in ("score_tools.py", "batch_scan.py", "policy_decisions.py", "temporal_policy.py"):
        print(name, sha256(root / "src" / name))
    before = sha256(database)
    # Restrict the original score tools in this process only; no disk edits.
    with patch.object(score_tools, "_connect", readonly_connection):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(IntegrationTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if sha256(database) != before:
        print("ERROR: database bytes changed during read-only checks", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
