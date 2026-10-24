"""Optional local integration tests (real score DB and Modules 5.1–5.3)."""

import json
import unittest
from pathlib import Path

from src.response_gate import handle_verification
from src.verification_pipeline import verify_structured_response



class RealGateSmokeTest(unittest.TestCase):
    def make_audit(self, score: int) -> dict:
        from src.score_tools import get_history
        history = get_history(1000003)
        self.assertTrue(history)
        payload = {
            "answer": (
                f"Account 1000003 had a score of {score} on 1991-12-01 "
                "in market 1. Its warning signal status is triggered."
            ),
            "score_claims": [{
                "account_id": 1000003, "period": "1991-12-01",
                "metric": "score", "value": score, "market": 1,
            }],
            "policy_claims": [{
                "field": "warning_signal_status", "value": "triggered",
                "account_id": 1000003, "as_of_period": "1991-12-01", "market": 1,
            }],
            "limitations": [],
        }
        evidence = [{
            "tool_name": "get_history",
            "arguments": {"account_id": 1000003},
            "result": history,
        }]
        return verify_structured_response(json.dumps(payload), evidence)

    def test_real_supported_claims_release_only_if_complete(self):
        audit = self.make_audit(265)
        self.assertEqual(audit["numeric"]["status"], "passed", audit)
        self.assertEqual(audit["policy"]["status"], "passed", audit)
        outcome = handle_verification(audit)
        # Real narrative parser is best-effort; it may reasonably mark a
        # valid narrative incomplete, which must still block publication.
        self.assertEqual(outcome["publishable"], audit["status"] == "passed", audit)

    def test_real_fabricated_score_is_never_released(self):
        audit = self.make_audit(999)
        outcome = handle_verification(audit)
        self.assertEqual(outcome["status"], "failed", audit)
        self.assertFalse(outcome["publishable"])
        self.assertNotIn("999", outcome["public_answer"])


if __name__ == "__main__":
    unittest.main()
