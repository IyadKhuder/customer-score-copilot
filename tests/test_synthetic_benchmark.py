"""V2 fixture contract tests: no original app or third-party imports needed."""

import copy
from pathlib import Path
import unittest

from src.synthetic_benchmark import (
    fingerprint, fixture_rows, load_contract, scripted_claims, validate_contract,
)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.benchmark, self.fixtures = load_contract()

    def test_identity_and_six_cases(self):
        self.assertEqual(self.benchmark["schema_version"], 2)
        self.assertEqual(len(self.benchmark["cases"]), 6)
        self.assertEqual(sum(c["expected"]["publishable"] for c in self.benchmark["cases"]), 5)
        self.assertEqual(self.benchmark["cases"][3]["expected"]["reason_codes"],
                         ["POLICY_EVIDENCE_INSUFFICIENT"])

    def test_bins_are_derived_per_observation(self):
        for fixture in self.fixtures["fixtures"]:
            self.assertEqual(fixture["bins"], [1+s//50 for s in fixture["scores"]])
        quality = self.fixtures["fixtures"][2]
        self.assertGreater(len(set(quality["bins"])), 1)

    def test_no_historical_ids_or_calendars(self):
        for fixture in self.fixtures["fixtures"]:
            self.assertTrue(all(p.startswith("1991-") for p in fixture["periods"]))
            self.assertIn(fixture["account_id"] // 1_000_000, (81, 83))
        self.assertEqual(len({f["account_id"] for f in self.fixtures["fixtures"]}), 6)

    def test_fixture_mismatch_is_rejected(self):
        for field, value in (("bins", [1]*10), ("scores", [True]*10),
                             ("account_id", True), ("market", 3),
                             ("policy_ids", ["POL-999"])):
            b, f = copy.deepcopy(self.benchmark), copy.deepcopy(self.fixtures)
            f["fixtures"][0][field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                validate_contract(b, f)

    def test_missing_or_wrong_month_is_rejected(self):
        f = copy.deepcopy(self.fixtures)
        f["fixtures"][0]["periods"][1] = "1991-05-01"
        with self.assertRaises(ValueError): validate_contract(self.benchmark, f)

    def test_relabeling_v1_or_mapping_is_rejected(self):
        for key, value in (("benchmark_id", "scorecopilot_claim_first_v1"),
                           ("schema_version", 1), ("mapping_version", "legacy")):
            b = copy.deepcopy(self.benchmark); b[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_contract(b, self.fixtures)

    def test_reordered_or_duplicate_cases_are_rejected(self):
        b = copy.deepcopy(self.benchmark); b["cases"].reverse()
        with self.assertRaises(ValueError): validate_contract(b, self.fixtures)
        b = copy.deepcopy(self.benchmark); b["cases"][1] = copy.deepcopy(b["cases"][0])
        with self.assertRaises(ValueError): validate_contract(b, self.fixtures)

    def test_malformed_expected_values_are_rejected(self):
        for key, value in (("consecutive_decline_event_count", True), ("publishable", 1),
                           ("warning_signal_status", "cleared"), ("required_policy_ids", [])):
            b = copy.deepcopy(self.benchmark); b["cases"][0]["expected"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_contract(b, self.fixtures)

    def test_rows_keep_individual_bins_and_market(self):
        fixture = self.fixtures["fixtures"][2]
        rows = fixture_rows(fixture)
        self.assertEqual(rows[0], {"period": "1991-03-01", "score": 392, "bin": 8, "market": 1})
        self.assertEqual(rows[-1], {"period": "1991-12-01", "score": 189, "bin": 4, "market": 1})

    def test_scripted_draft_is_untrusted_and_claims_are_explicit(self):
        for case, fixture in zip(self.benchmark["cases"], self.fixtures["fixtures"]):
            claims = scripted_claims(case, fixture)
            self.assertIn("UNTRUSTED", claims["answer"])
            self.assertEqual(claims["limitations"], [])
            for claim in claims["score_claims"]:
                self.assertEqual(claim["period"], "1991-12-01")
                self.assertEqual(claim["account_id"], fixture["account_id"])

    def test_fingerprint_tracks_changes_not_object_identity(self):
        self.assertEqual(fingerprint(self.fixtures), fingerprint(copy.deepcopy(self.fixtures)))
        changed = copy.deepcopy(self.fixtures); changed["fixtures"][0]["scores"][-1] += 1
        self.assertNotEqual(fingerprint(changed), fingerprint(self.fixtures))


if __name__ == "__main__": unittest.main()
