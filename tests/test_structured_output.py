import json
import unittest

from src.structured_output import StructuredOutputError, parse_structured_output


VALID = {
    "answer": "Account 81000001 has a score of 271 in April 2025.",
    "score_claims": [
        {"account_id": 81000001, "period": "2025-04-01", "metric": "score", "value": 271, "market": 1}
    ],
    "policy_claims": [
        {"field": "warning_signal_status", "value": "triggered", "account_id": 81000001,
         "as_of_period": "2025-04-01", "market": 1}
    ],
    "limitations": ["The administrative warning-register status is unavailable."],
}


class StructuredOutputTests(unittest.TestCase):
    def test_valid_response(self):
        self.assertEqual(parse_structured_output(json.dumps(VALID)), VALID)

    def test_missing_claim_group_is_rejected(self):
        sample = {key: val for key, val in VALID.items() if key != "policy_claims"}
        with self.assertRaises(StructuredOutputError):
            parse_structured_output(json.dumps(sample))

    def test_invalid_date_is_rejected(self):
        sample = json.loads(json.dumps(VALID))
        sample["score_claims"][0]["period"] = "2025-02-30"
        with self.assertRaises(StructuredOutputError):
            parse_structured_output(json.dumps(sample))

    def test_wrong_type_is_rejected(self):
        sample = json.loads(json.dumps(VALID))
        sample["score_claims"][0]["value"] = True
        with self.assertRaises(StructuredOutputError):
            parse_structured_output(json.dumps(sample))

    def test_plain_prose_is_rejected(self):
        with self.assertRaises(StructuredOutputError):
            parse_structured_output("The account seems stable.")

    def test_empty_claim_lists_are_structurally_valid(self):
        sample = json.loads(json.dumps(VALID))
        sample["score_claims"] = []
        sample["policy_claims"] = []
        self.assertEqual(parse_structured_output(json.dumps(sample)), sample)
        # Passing the structure does NOT imply evidence coverage.


if __name__ == "__main__":
    unittest.main()
