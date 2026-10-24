"""Real-agent replay and adversarial publication tests for new v2 fixtures."""

import argparse
import copy
import importlib
from pathlib import Path
import socket
import sqlite3
import sys
from types import ModuleType
import unittest

from benchmark_backend import offline_application, execute_case, sanitize, evaluate_observation

package = ModuleType("synthetic_fixture_package")
package.__path__ = [str(Path(__file__).resolve().parents[1]/"src")]
sys.modules[package.__name__] = package
contract = importlib.import_module("synthetic_fixture_package.synthetic_benchmark")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    args = parser.parse_args()
    benchmark, fixtures = contract.load_contract()
    with offline_application(args.source_root) as app:
        def run(index=0, edit=None, missing=()):
            case, fixture = benchmark["cases"][index], fixtures["fixtures"][index]
            claims = contract.scripted_claims(case, fixture)
            if edit: edit(claims)
            return execute_case(app, case, fixture, contract.fixture_rows(fixture), claims,
                                omit_policies=missing)

        class SafetyTests(unittest.TestCase):
            def test_all_six_cases_match_independent_expectations(self):
                for index, (case, fixture) in enumerate(zip(benchmark["cases"], fixtures["fixtures"])):
                    with self.subTest(case=case["id"]):
                        request = case["request"]
                        kwargs = {k: request[k] for k in ("site_onboarded_period", "last_exception_review_period")
                                  if k in request}
                        decision = app.decide(contract.fixture_rows(fixture), request["as_of_period"], **kwargs)
                        checks, _ = evaluate_observation(case, sanitize(run(index)), decision)
                        self.assertTrue(all(checks.values()), checks)

            def test_wrong_numeric_score_blocks(self):
                result = run(edit=lambda c: c["score_claims"][0].update(value=449))
                self.assertFalse(result["publishable"])

            def test_wrong_numeric_bin_blocks(self):
                result = run(edit=lambda c: c["score_claims"][1].update(value=9))
                self.assertFalse(result["publishable"])

            def test_wrong_event_count_blocks(self):
                def change(c):
                    next(p for p in c["policy_claims"] if p["field"] == "raw_event_count.consecutive_declines")["value"] = 99
                self.assertFalse(run(edit=change)["publishable"])

            def test_wrong_claim_date_blocks(self):
                self.assertFalse(run(edit=lambda c: c["score_claims"][0].update(period="1991-11-01"))["publishable"])

            def test_wrong_account_context_blocks(self):
                self.assertFalse(run(edit=lambda c: c["score_claims"][0].update(account_id=999))["publishable"])

            def test_missing_policy_source_blocks(self):
                self.assertFalse(run(2, missing=("POL-005",))["publishable"])

            def test_insufficient_history_blocks_and_sanitizes(self):
                result = run(3)
                self.assertFalse(result["publishable"])
                self.assertEqual(result["reason_codes"], ["POLICY_EVIDENCE_INSUFFICIENT"])
                self.assertEqual(sanitize(result)["public_answer"], "")

            def test_untrusted_draft_is_excluded_from_published_answer(self):
                result = run()
                self.assertTrue(result["publishable"])
                self.assertNotIn("UNTRUSTED", result["answer"])
                self.assertNotIn("automatically approve credit", result["answer"])

            def test_network_and_database_backends_are_disabled(self):
                with self.assertRaises(RuntimeError): socket.socket()
                with self.assertRaises(RuntimeError): sqlite3.connect(":memory:")

        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(SafetyTests))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__": raise SystemExit(main())
