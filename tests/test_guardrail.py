
import unittest

from src.guardrail import (
    ScoreClaim,
    check_score_claim,
    extract_score_claims,
    audit_answer,
)


EVIDENCE = [{
    "tool_name": "get_history",
    "arguments": {"account_id": 81000001},
    "result": [
        {"period": "2023-06-01", "score": 611,
         "bin": 7, "market": 1},
        {"period": "2023-07-01", "score": 636,
         "bin": 7, "market": 1},
    ],
}]


class GuardrailTests(unittest.TestCase):

    def test_valid_claim(self):
        claim = ScoreClaim(
            81000001, "2023-06-01", "score", 611, market=1
        )

        self.assertEqual(check_score_claim(claim, EVIDENCE), {
            "value_grounded": True,
            "context_grounded": True,
        })

    def test_swapped_months(self):
        swapped_claims = [
            ScoreClaim(81000001, "2023-06-01", "score", 636, 1),
            ScoreClaim(81000001, "2023-07-01", "score", 611, 1),
        ]

        for claim in swapped_claims:
            self.assertEqual(check_score_claim(claim, EVIDENCE), {
                "value_grounded": True,
                "context_grounded": False,
            })

    def test_fabricated_score(self):
        claim = ScoreClaim(
            81000001, "2023-06-01", "score", 999, market=1
        )

        self.assertEqual(check_score_claim(claim, EVIDENCE), {
            "value_grounded": False,
            "context_grounded": False,
        })


    def test_wrong_account(self):
        """Valid score, but attributed to a different account."""
        claim = ScoreClaim(
            203480, "2023-06-01", "score", 611, market=1
        )

        self.assertEqual(check_score_claim(claim, EVIDENCE), {
            "value_grounded": True,
            "context_grounded": False,
        })

    def test_wrong_metric(self):
        """Bin value 7 incorrectly reported as a score."""
        claim = ScoreClaim(
            81000001, "2023-06-01", "score", 7, market=1
        )

        self.assertEqual(check_score_claim(claim, EVIDENCE), {
            "value_grounded": False,
            "context_grounded": False,
        })

    def test_wrong_market(self):
        """Valid score, but attributed to market 2 instead of 1."""
        claim = ScoreClaim(
            81000001, "2023-06-01", "score", 611, market=2
        )

        self.assertEqual(check_score_claim(claim, EVIDENCE), {
            "value_grounded": True,
            "context_grounded": False,
        })


    def test_extract_multiple_claims(self):
        answer = (
            "Account 81000001 had a score of 611 on 2023-06-01 "
            "in market 1. "
            "Account 81000001 had a score of 636 on 2023-07-01 "
            "in market 1."
        )

        claims = extract_score_claims(answer)

        self.assertEqual(claims, [
            ScoreClaim(81000001, "2023-06-01", "score", 611, 1),
            ScoreClaim(81000001, "2023-07-01", "score", 636, 1),
        ])

        for claim in claims:
            result = check_score_claim(claim, EVIDENCE)
            self.assertTrue(result["context_grounded"])

    def test_extract_swapped_claim(self):
        answer = (
            "Account 81000001 had a score of 636 "
            "on 2023-06-01 in market 1."
        )

        claims = extract_score_claims(answer)

        self.assertEqual(len(claims), 1)

        result = check_score_claim(claims[0], EVIDENCE)

        self.assertTrue(result["value_grounded"])
        self.assertFalse(result["context_grounded"])

    def test_audit_fully_supported_format(self):
        answer = (
            "Account 81000001 had a score of 611 on 2023-06-01 "
            "in market 1. "
            "Account 81000001 had a score of 636 on 2023-07-01 "
            "in market 1."
        )

        result = audit_answer(answer, EVIDENCE)

        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(len(result["checks"]), 2)

    def test_audit_wrong_context(self):
        answer = (
            "Account 81000001 had a score of 636 "
            "on 2023-06-01 in market 1."
        )

        result = audit_answer(answer, EVIDENCE)

        self.assertEqual(result["status"], "failed")
        self.assertFalse(
            result["checks"][0]["context_grounded"]
        )

    def test_audit_partially_unparsed(self):
        answer = (
            "Account 81000001 had a score of 611 "
            "on 2023-06-01 in market 1. "
            "In July 2023, its score fell by 80 points."
        )

        result = audit_answer(answer, EVIDENCE)

        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(len(result["checks"]), 1)
        self.assertIn(
            "80 points", result["unchecked_text"][0]
        )

    def test_audit_no_extracted_claims(self):
        answer = (
            "In June 2023, account 81000001 scored 611."
        )

        result = audit_answer(answer, EVIDENCE)

        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(result["checks"]), 0)


if __name__ == "__main__":
    unittest.main()
