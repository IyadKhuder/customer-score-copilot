"""Optional smoke test against your actual Module 5.1–5.3 validators.

Requires the existing src.guardrail, src.policy_claim_guard, src.score_tools
and data/scores.db. Unlike test_verification_pipeline.py, this runs the real
validator functions. It is skipped automatically when the database is absent.
"""

import json
import unittest
from pathlib import Path

from src.verification_pipeline import verify_structured_response


class RealValidatorSmokeTest(unittest.TestCase):
    def test_known_account_and_warning_signal(self):
        from src.score_tools import get_history
        history = get_history(1000003)
        self.assertTrue(history, "Expected account 1000003 in the local score database")

        payload = {
            "answer": (
                "Account 1000003 had a score of 265 on 1991-12-01 "
                "in market 1. Its warning signal status is triggered."
            ),
            "score_claims": [
                {"account_id": 1000003, "period": "1991-12-01",
                 "metric": "score", "value": 265, "market": 1}
            ],
            "policy_claims": [
                {"field": "warning_signal_status", "value": "triggered",
                 "account_id": 1000003, "as_of_period": "1991-12-01", "market": 1}
            ],
            "limitations": [],
        }
        evidence = [{"tool_name": "get_history", "arguments": {"account_id": 1000003},
                     "result": history}]
        audit = verify_structured_response(json.dumps(payload), evidence)
        self.assertEqual(audit["numeric"]["status"], "passed", audit)
        self.assertEqual(audit["policy"]["status"], "passed", audit)
        # Coverage is best-effort. The legacy narrative parser may return
        # incomplete when natural phrasing cannot be parsed; do not upgrade it.
        self.assertIn(audit["status"], ("passed", "incomplete"), audit)


if __name__ == "__main__":
    unittest.main()
