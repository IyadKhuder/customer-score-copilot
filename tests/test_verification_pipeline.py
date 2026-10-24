"""Offline contract tests for Module 5.4.2 orchestration.

Uses deterministic stand-ins for pre-existing guardrail modules. This tests
wiring/status composition, not the internal correctness of those validators.
The user's existing Module 5.1–5.3 test suite covers the validator logic.
"""

import copy
import json
import sys
import types
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from src.verification_pipeline import verify_structured_response


EVIDENCE = [{
    "tool_name": "get_history",
    "arguments": {"account_id": 81000001},
    "result": [
        {"period": "2025-03-01", "score": 267, "bin": 6, "market": 1},
        {"period": "2025-04-01", "score": 271, "bin": 6, "market": 1},
    ],
}]

VALID = {
    "answer": "Account 81000001 had a score of 271 on 2025-04-01 in market 1.",
    "score_claims": [{"account_id": 81000001, "period": "2025-04-01",
                      "metric": "score", "value": 271, "market": 1}],
    "policy_claims": [{"field": "warning_signal_status", "value": "triggered",
                       "account_id": 81000001, "as_of_period": "2025-04-01", "market": 1}],
    "limitations": [],
}


@dataclass(frozen=True)
class FakeScoreClaim:
    account_id: int
    period: str
    metric: str
    value: int
    market: int


def fake_score_check(claim, evidence):
    rows = evidence[0]["result"]
    same_value = any(row[claim.metric] == claim.value for row in rows)
    same_context = any(row["period"] == claim.period and row["market"] == claim.market
                       and row[claim.metric] == claim.value for row in rows)
    return {"value_grounded": same_value, "context_grounded": same_context}


def fake_extract(answer):
    # Deliberately mirrors only the canonical format used in these tests.
    import re
    pattern = r"Account (\d+) had a score of (\d+) on (\d{4}-\d\d-\d\d) in market (\d+)"
    return [FakeScoreClaim(int(a), d, "score", int(s), int(m))
            for a, s, d, m in re.findall(pattern, answer)]


def fake_narrative(answer, evidence):
    if "unparsed 80-point decline" in answer:
        return {"status": "incomplete", "coverage_complete": False,
                "checks": [], "unchecked_text": ["unparsed 80-point decline"]}
    return {"status": "passed", "coverage_complete": True, "checks": []}


def fake_policy(claims, history, account_id, as_of_period):
    claim = claims[0]
    assert account_id == claim["account_id"]
    assert as_of_period == claim["as_of_period"]
    if claim["field"] != "warning_signal_status":
        return {"status": "incomplete", "checks": [{"field": claim["field"], "passed": False}]}
    expected = "triggered"
    passed = claim["value"] == expected
    return {"status": "passed" if passed else "failed",
            "checks": [{"field": claim["field"], "claimed": claim["value"],
                        "expected": expected, "context_matches": True,
                        "value_matches": passed, "passed": passed}]}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        guard = types.ModuleType("src.guardrail")
        guard.ScoreClaim = FakeScoreClaim
        guard.check_score_claim = fake_score_check
        guard.extract_score_claims = fake_extract
        guard.audit_answer = fake_narrative
        policy = types.ModuleType("src.policy_claim_guard")
        policy.audit_policy_claims = fake_policy
        self.patcher = patch.dict(sys.modules, {"src.guardrail": guard,
                                                "src.policy_claim_guard": policy})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def verify(self, data=None, evidence=None):
        return verify_structured_response(json.dumps(VALID if data is None else data),
                                          EVIDENCE if evidence is None else evidence)

    def test_fully_supported(self):
        audit = self.verify()
        self.assertEqual(audit["status"], "passed")
        self.assertEqual(audit["numeric"]["status"], "passed")
        self.assertEqual(audit["policy"]["status"], "passed")
        self.assertEqual(audit["narrative"]["status"], "passed")
        self.assertEqual(audit["answer"], VALID["answer"])

    def test_fabricated_score(self):
        data = copy.deepcopy(VALID)
        data["score_claims"][0]["value"] = 999
        self.assertEqual(self.verify(data)["status"], "failed")

    def test_swapped_period(self):
        data = copy.deepcopy(VALID)
        data["score_claims"][0]["period"] = "2025-03-01"
        self.assertEqual(self.verify(data)["numeric"]["status"], "failed")

    def test_false_policy_claim(self):
        data = copy.deepcopy(VALID)
        data["policy_claims"][0]["value"] = "not_triggered"
        audit = self.verify(data)
        self.assertEqual(audit["status"], "failed")
        self.assertEqual(audit["policy"]["checks"][0]["audit"]["checks"][0]["expected"], "triggered")

    def test_unsupported_policy_claim_is_incomplete(self):
        data = copy.deepcopy(VALID)
        data["policy_claims"][0]["field"] = "imaginary_policy_field"
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_missing_history_is_incomplete(self):
        audit = self.verify(evidence=[])
        self.assertEqual(audit["numeric"]["status"], "incomplete")
        self.assertEqual(audit["policy"]["status"], "incomplete")
        self.assertEqual(audit["status"], "incomplete")

    def test_claim_market_conflict_is_failure(self):
        data = copy.deepcopy(VALID)
        data["policy_claims"][0]["market"] = 3
        self.assertEqual(self.verify(data)["policy"]["status"], "failed")

    def test_unknown_as_of_period_is_incomplete(self):
        data = copy.deepcopy(VALID)
        data["policy_claims"][0]["as_of_period"] = "2025-05-01"
        self.assertEqual(self.verify(data)["policy"]["status"], "incomplete")

    def test_empty_claim_lists_are_not_passed(self):
        data = copy.deepcopy(VALID)
        data["score_claims"] = []
        data["policy_claims"] = []
        audit = self.verify(data)
        self.assertEqual(audit["status"], "incomplete")
        self.assertTrue(audit["narrative"]["omitted_score_claims"])

    def test_narrative_claim_missing_from_list_is_incomplete(self):
        data = copy.deepcopy(VALID)
        data["score_claims"] = []
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_narrative_unchecked_text_is_incomplete(self):
        data = copy.deepcopy(VALID)
        data["answer"] += " There was an unparsed 80-point decline."
        self.assertEqual(self.verify(data)["narrative"]["status"], "incomplete")
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_bad_schema_is_incomplete_not_exception(self):
        audit = verify_structured_response("not JSON", EVIDENCE)
        self.assertEqual(audit["status"], "incomplete")
        self.assertIsNone(audit["answer"])


if __name__ == "__main__":
    unittest.main()
