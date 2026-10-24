"""Read-only compatibility checks against the owner's existing source snapshot.

Run from this package: python validation/check_current_policy.py --source-root PATH
Only three standard-library policy/scan modules are imported. No database or API.
"""

import argparse
import hashlib
from pathlib import Path
import sys
import unittest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.source_root.resolve()
    # Importing the existing source must not write bytecode into that project.
    sys.dont_write_bytecode = True
    required = ("temporal_policy.py", "policy_decisions.py", "batch_scan.py")
    if not all((root / "src" / name).is_file() for name in required):
        parser.error("source root must contain src/ and the three policy/scan modules")
    # Load the new, pure constructors alongside the existing src package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    sys.path.insert(0, str(root))
    from synthetic_foundations import build_anchor
    from src.policy_decisions import evaluate_policy_decision
    from src.batch_scan import InvalidScanRequest, scan_batch, validate_request

    def history(scenario, market=1):
        return [r.as_dict() for r in build_anchor(scenario, market=market)]

    def decision(scenario, market=1):
        kwargs = {} if market == 1 else {
            "site_onboarded_period": "1991-01-01" if market == 3 else "1986-01-01",
            "last_exception_review_period": "1991-10-01",
        }
        return evaluate_policy_decision(history(scenario, market), "1991-12-01", **kwargs)

    class CompatibilityTests(unittest.TestCase):
        def test_stable_and_improving_are_negative_controls(self):
            for scenario in ("stable", "improving"):
                with self.subTest(scenario=scenario):
                    d = decision(scenario)
                    self.assertEqual(d["warning_signal_status"], "not_triggered")
                    self.assertEqual(d["data_quality_review"], "not_flagged")
                    self.assertTrue(d["coverage_complete"])

        def test_decline_is_eligible(self):
            d = decision("declining")
            self.assertEqual(d["warning_signal_status"], "triggered")
            self.assertEqual(d["data_quality_review"], "not_flagged")
            self.assertEqual(sum(e["criterion"] == "consecutive_declines"
                                 for e in d["events"]), 3)

        def test_sudden_drop_has_no_severe_three_bin_event(self):
            d = decision("sudden_drop")
            self.assertEqual(d["committee_review_account_rules"], "indicated")
            self.assertEqual(d["severe_downward_migrations"], [])
            self.assertEqual(d["data_quality_review"], "not_flagged")

        def test_severe_drop_remains_eligible(self):
            d = decision("severe_drop")
            self.assertEqual(d["committee_review_account_rules"], "indicated")
            self.assertEqual(d["data_quality_review"], "not_flagged")
            self.assertEqual([(e["period"], e["bin_change"], e["review_state"])
                              for e in d["severe_downward_migrations"]],
                             [("1991-10-01", -3, "eligible")])

        def test_quality_and_volatility_retain_holds(self):
            for scenario in ("volatile", "data_quality"):
                with self.subTest(scenario=scenario):
                    d = decision(scenario)
                    self.assertEqual(d["data_quality_review"], "required")
                    self.assertEqual(d["risk_action_gate"], "hold_pending_data_quality")
                    self.assertEqual(d["committee_review_account_rules"], "pending_data_quality")

        def test_recovery_does_not_assert_reinstatement(self):
            d = decision("recovery")
            self.assertEqual(d["warning_signal_status"], "not_triggered")
            self.assertNotIn("reinstatement", d)
            self.assertEqual(d["administrative_warning_status"], "not_determined")

        def test_coverage_gaps_are_insufficient(self):
            d = decision("missing_interior")
            self.assertEqual(d["missing_baseline_months"], ["1991-10-01"])
            self.assertEqual(d["warning_signal_status"], "insufficient_evidence")
            d = decision("short_history")
            self.assertEqual(len(d["missing_baseline_months"]), 6)
            self.assertEqual(d["warning_signal_status"], "insufficient_evidence")
            with self.assertRaises(ValueError):
                decision("missing_latest")

        def test_explicit_market_metadata(self):
            self.assertEqual(decision("stable", 2)["policy_applicability"]["mode"], "standard")
            applicability = decision("stable", 3)["policy_applicability"]
            self.assertEqual(applicability["mode"], "grace")
            self.assertEqual(applicability["quarterly_exception_review"], "current")
            self.assertEqual(applicability["thresholds"], {
                "score_drop": 50, "bin_drop": 3,
                "consecutive_declines": 4, "volatility": 43.75,
            })

        def test_scan_scope_is_preserved(self):
            def request(market, count):
                return dict(schema_version=1, account_ids=list(range(1, count + 1)),
                            market=market, as_of_period="1991-12-01")
            self.assertEqual(len(validate_request(request(1, 25))["account_ids"]), 25)
            for market, count in ((1, 26), (2, 1), (3, 1)):
                with self.subTest(market=market, count=count), self.assertRaises(InvalidScanRequest):
                    validate_request(request(market, count))

        def test_actual_scanner_with_temporary_anchor_database(self):
            from contextlib import closing
            import sqlite3
            from tempfile import TemporaryDirectory
            from synthetic_foundations import build_default_anchors, anchor_account_id

            with TemporaryDirectory() as temporary:
                db = Path(temporary) / "anchors.db"
                # The inner context commits/rolls back; closing releases the
                # handle on success AND error, before Windows temp cleanup.
                with closing(sqlite3.connect(db)) as conn:
                    with conn:
                        conn.execute("CREATE TABLE scores (account_id INTEGER, period TEXT, "
                                     "score INTEGER, bin INTEGER, market INTEGER, "
                                     "PRIMARY KEY (account_id, period))")
                        conn.executemany("INSERT INTO scores VALUES (?, ?, ?, ?, ?)",
                                         [(r.account_id, r.period, r.score, r.bin, r.market)
                                          for r in build_default_anchors()])
                # Detect an unclosed connection on Linux too, where unlinking
                # an open database may otherwise hide this Windows failure.
                with self.assertRaises(sqlite3.ProgrammingError):
                    conn.execute("SELECT 1")
                scenarios = ("stable", "declining", "severe_drop", "data_quality",
                             "missing_interior", "missing_latest", "short_history")
                ids = [anchor_account_id(s) for s in scenarios]
                report = scan_batch(dict(schema_version=1, account_ids=ids, market=1,
                                         as_of_period="1991-12-01"), db, source_root=root)
                self.assertEqual(report["run_status"], "completed", report.get("run_error"))
                outcomes = {r["account_id"]: r["scan_outcome"] for r in report["results"]}
                expected = ("no_signal_in_scope", "attention_required", "attention_required",
                            "attention_required", "insufficient_data", "insufficient_data",
                            "insufficient_data")
                self.assertEqual([outcomes[i] for i in ids], list(expected))
                self.assertTrue(all(r["narrative"]["publishable"] is False
                                    for r in report["results"]))

    for name in required:
        print(name, hashlib.sha256((root / "src" / name).read_bytes()).hexdigest())
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CompatibilityTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
