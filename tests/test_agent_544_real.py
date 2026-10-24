"""Optional local integration test using the REAL DB and policy validators.

The LLM is scripted (no API charges), but all tool calls, score/policy audits,
and the publication gate run through the actual project components.
"""

import copy
import json
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from src.agent import run_agent


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class HistoryThenJSON:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, messages, tools=None):
        return Reply(tool_calls=[
            ToolCall("history_1", "get_history", {"account_id": 1000003}),
            ToolCall("policy_1", "retrieve_policy",
                     {"query": "early-warning decline signal POL-002"}),
        ])

    def continue_with_tool_results(self, assistant_response, tool_results, tools=None):
        assert len(tool_results) == 2 and tool_results[0], "No history returned"
        return Reply(text=json.dumps(self.payload))



class RealAgentSmokeTests(unittest.TestCase):
    def setUp(self):
        try:
            from src import guardrail, policy_claim_guard
        except ImportError:
            self.fail("Local score and policy validators are unavailable")
        self.payload = {
            "answer": (
                "Account 1000003 had a score of 265 on 1991-12-01 in market 1. "
                "Its warning signal status is triggered."
            ),
            "score_claims": [{"account_id": 1000003, "period": "1991-12-01",
                              "metric": "score", "value": 265, "market": 1}],
            "policy_claims": [{"field": "warning_signal_status", "value": "triggered",
                               "account_id": 1000003, "as_of_period": "1991-12-01",
                               "market": 1}],
            "limitations": [],
        }

    def test_true_score_is_verified_and_gate_agrees_with_audit(self):
        result = run_agent("Account 1000003, December 1991", HistoryThenJSON(self.payload),
                           verbose=False)
        self.assertTrue(result["completed"])
        self.assertEqual(len(result["tool_evidence"]), 2)
        self.assertEqual(result["verification"]["numeric"]["status"], "passed")
        self.assertEqual(result["verification"]["policy"]["status"], "passed")
        # A policy passage must also be retrieved, not just calculated.
        self.assertEqual(result["publishable"], result["status"] == "passed")

    def test_fabricated_score_never_released(self):
        payload = copy.deepcopy(self.payload)
        payload["answer"] = payload["answer"].replace("265", "999")
        payload["score_claims"][0]["value"] = 999
        result = run_agent("Account 1000003, December 1991", HistoryThenJSON(payload),
                           verbose=False)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("999", result["answer"])


if __name__ == "__main__":
    unittest.main()
