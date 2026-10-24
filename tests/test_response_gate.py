"""Module 5.4.3: fail-closed publication behavior (fully offline)."""

import copy
import unittest

from src.response_gate import handle_verification


PASS = {
    "status": "passed",
    "answer": "Account 81000001 had a score of 271 on 2025-04-01.",
    "schema": {"status": "passed"},
    "numeric": {"status": "passed", "checks": [{
        "status": "passed", "context_grounded": True,
    }]},
    "policy": {"status": "passed", "checks": [{
        "status": "passed", "audit": {"status": "passed", "checks": [{"passed": True}]},
    }]},
    "narrative": {"status": "passed", "coverage_complete": True,
                  "omitted_score_claims": [], "unchecked_text": []},
    "issues": [],
    "limitations": [],
}


class ResponseGateTests(unittest.TestCase):
    def build(self):
        return copy.deepcopy(PASS)

    def test_passed_answer_is_released(self):
        decision = handle_verification(self.build())
        self.assertTrue(decision["publishable"])
        self.assertEqual(decision["public_answer"], PASS["answer"])
        self.assertEqual(decision["next_action"], "publish")

    def test_numeric_failure_blocks_raw_answer(self):
        report = self.build()
        report["status"] = report["numeric"]["status"] = "failed"
        report["numeric"]["checks"][0]["status"] = "failed"
        decision = handle_verification(report)
        self.assertEqual(decision["status"], "failed")
        self.assertIn("NUMERIC_MISMATCH", decision["reason_codes"])
        self.assertFalse(decision["publishable"])
        self.assertNotIn("81000001", decision["public_answer"])

    def test_policy_failure_blocks_raw_answer(self):
        report = self.build()
        report["status"] = report["policy"]["status"] = "failed"
        report["policy"]["checks"][0]["status"] = "failed"
        decision = handle_verification(report)
        self.assertEqual(decision["status"], "failed")
        self.assertEqual(decision["next_action"], "revise_response")

    def test_bad_json_report_recommends_regeneration(self):
        report = self.build()
        report.update(status="incomplete", answer=None, schema={"status": "incomplete"})
        decision = handle_verification(report)
        self.assertFalse(decision["publishable"])
        self.assertEqual(decision["next_action"], "regenerate_json")

    def test_missing_numeric_history_requests_evidence(self):
        report = self.build()
        report["status"] = report["numeric"]["status"] = "incomplete"
        report["numeric"]["checks"] = [{"status": "incomplete",
                                           "value_grounded": False,
                                           "context_grounded": False}]
        decision = handle_verification(report)
        self.assertEqual(decision["status"], "incomplete")
        self.assertEqual(decision["next_action"], "gather_evidence")

    def test_missing_policy_history_requests_evidence(self):
        report = self.build()
        report["status"] = report["policy"]["status"] = "incomplete"
        report["policy"]["checks"] = [{"status": "incomplete", "audit": {
            "reason": "No get_history evidence for account and as_of_period",
        }}]
        decision = handle_verification(report)
        self.assertEqual(decision["next_action"], "gather_evidence")

    def test_unsupported_policy_claim_requires_review(self):
        report = self.build()
        report["status"] = report["policy"]["status"] = "incomplete"
        report["policy"]["checks"] = [{"status": "incomplete", "audit": {
            "status": "incomplete", "checks": [{"passed": False}],
        }}]
        self.assertEqual(handle_verification(report)["next_action"], "manual_review")

    def test_unchecked_narrative_blocks_answer(self):
        report = self.build()
        report["status"] = report["narrative"]["status"] = "incomplete"
        report["narrative"]["coverage_complete"] = False
        report["narrative"]["unchecked_text"] = ["Claim not checked"]
        decision = handle_verification(report)
        self.assertEqual(decision["next_action"], "revise_response")
        self.assertFalse(decision["publishable"])

    def test_omitted_narrative_claim_blocks_even_if_status_says_passed(self):
        report = self.build()
        report["narrative"]["omitted_score_claims"] = [{"value": 999}]
        decision = handle_verification(report)
        self.assertEqual(decision["status"], "incomplete")
        self.assertFalse(decision["publishable"])

    def test_no_checks_cannot_be_published(self):
        report = self.build()
        report["numeric"] = {"status": "not_applicable", "checks": []}
        report["policy"] = {"status": "not_applicable", "checks": []}
        decision = handle_verification(report)
        self.assertIn("NO_CHECKED_CLAIMS", decision["reason_codes"])
        self.assertFalse(decision["publishable"])

    def test_status_cannot_hide_subcheck_failure(self):
        report = self.build()
        report["numeric"]["checks"][0]["status"] = "failed"
        decision = handle_verification(report)
        self.assertEqual(decision["status"], "failed")
        self.assertFalse(decision["publishable"])

    def test_passed_policy_status_with_failed_inner_check_is_blocked(self):
        report = self.build()
        report["policy"]["checks"][0]["audit"]["checks"][0]["passed"] = False
        decision = handle_verification(report)
        self.assertFalse(decision["publishable"])
        self.assertEqual(decision["status"], "incomplete")

    def test_missing_report_is_incomplete(self):
        decision = handle_verification(None)
        self.assertEqual(decision["status"], "incomplete")
        self.assertFalse(decision["publishable"])

    def test_malformed_report_status_fails_closed(self):
        report = self.build()
        report["numeric"]["status"] = {"unexpected": "dict"}
        decision = handle_verification(report)
        self.assertFalse(decision["publishable"])
        self.assertEqual(decision["status"], "incomplete")

    def test_malformed_check_collection_fails_closed(self):
        report = self.build()
        report["policy"]["checks"] = None
        decision = handle_verification(report)
        self.assertEqual(decision["status"], "incomplete")
        self.assertFalse(decision["publishable"])

    def test_raw_issues_block_publishing(self):
        report = self.build()
        report["issues"] = ["Missing claim"]
        self.assertFalse(handle_verification(report)["publishable"])

    def test_numeric_only_is_allowed_when_fully_audited(self):
        report = self.build()
        report["policy"] = {"status": "not_applicable", "checks": []}
        self.assertTrue(handle_verification(report)["publishable"])

    def test_top_level_failed_cannot_be_upgraded_to_passed(self):
        report = self.build()
        report["status"] = "failed"
        decision = handle_verification(report)
        self.assertEqual(decision["status"], "failed")
        self.assertFalse(decision["publishable"])


if __name__ == "__main__":
    unittest.main()
