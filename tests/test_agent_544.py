"""Module 5.4.4: provider-independent end-to-end orchestration (offline).

Tests exercise REAL parser, REAL coordinator and REAL response gate using
small deterministic score and policy validator stand-ins. No LLM API key or
SQLite database is required. The existing tests cover the true validators.
"""
import copy
import json
import re
import sys
import types
import unittest
from dataclasses import dataclass, field
from unittest.mock import patch

from src import agent

HISTORY = [
    {"period": "2025-03-01", "score": 267, "bin": 6, "market": 1},
    {"period": "2025-04-01", "score": 271, "bin": 6, "market": 1},
]

PAYLOAD = {
    "answer": "Account 81000001 had a score of 271 on 2025-04-01 in market 1.",
    "score_claims": [{"account_id": 81000001, "period": "2025-04-01",
                      "metric": "score", "value": 271, "market": 1}],
    "policy_claims": [{"field": "warning_signal_status", "value": "triggered",
                       "account_id": 81000001, "as_of_period": "2025-04-01", "market": 1}],
    "limitations": [],
}


@dataclass(frozen=True)
class ScoreClaim:
    account_id: int
    period: str
    metric: str
    value: int
    market: int


def check_score_claim(claim, tool_evidence):
    histories = [item for item in tool_evidence if item["tool_name"] == "get_history"]
    rows = [row for item in histories if item["arguments"]["account_id"] == claim.account_id
            for row in item["result"]]
    return {
        "value_grounded": any(row[claim.metric] == claim.value for row in rows),
        "context_grounded": any(row[claim.metric] == claim.value
                                and row["period"] == claim.period
                                and row["market"] == claim.market for row in rows),
    }


def extract_score_claims(answer):
    pattern = r"Account (\d+) had a score of (\d+) on (\d{4}-\d\d-\d\d) in market (\d+)"
    return [ScoreClaim(int(a), p, "score", int(s), int(m))
            for a, s, p, m in re.findall(pattern, answer)]


def audit_answer(answer, evidence):
    claims = extract_score_claims(answer)
    if "unparsed 80-point decline" in answer:
        return {"status": "incomplete", "coverage_complete": False,
                "unchecked_text": ["unparsed 80-point decline"], "checks": []}
    for c in claims:
        if not check_score_claim(c, evidence)["context_grounded"]:
            return {"status": "failed", "coverage_complete": True,
                    "unchecked_text": [], "checks": []}
    return {"status": "passed", "coverage_complete": True,
            "unchecked_text": [], "checks": []}


def audit_policy_claims(claims, history, account_id, as_of_period):
    assert history and max(row["period"] for row in history) <= as_of_period, (
        "Future observations leaked into a historical decision"
    )
    claim = claims[0]
    if claim["field"] != "warning_signal_status":
        return {"status": "incomplete", "checks": [{"passed": False}]}
    passed = claim["value"] == "triggered"
    return {"status": "passed" if passed else "failed",
            "checks": [{"passed": passed}]}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class ScriptedLLM:
    """Adapter-compatible fake; scripts returned messages, not vendor SDKs."""
    def __init__(self, *responses):
        self.responses = list(responses)
        self.rounds = 0
        self.initial_messages = None
        self.results_seen = []

    def chat(self, messages, tools=None):
        self.initial_messages = messages
        assert len(tools) == 7
        return self.responses.pop(0)

    def continue_with_tool_results(self, assistant_response, tool_results, tools=None):
        assert len(tool_results) == len(assistant_response.tool_calls)
        self.rounds += 1
        self.results_seen.extend(tool_results)
        return self.responses.pop(0)


class InfiniteLLM:
    def chat(self, messages, tools=None):
        return Reply(tool_calls=[ToolCall("bad", "unknown_tool", {})])

    def continue_with_tool_results(self, assistant_response, tool_results, tools=None):
        assert "error" in tool_results[0]
        return Reply(tool_calls=[ToolCall("bad", "unknown_tool", {})])


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        # Provide Modules 5.1-5.3 through test stand-ins, not LLM reasoning.
        guard = types.ModuleType("src.guardrail")
        guard.ScoreClaim = ScoreClaim
        guard.check_score_claim = check_score_claim
        guard.extract_score_claims = extract_score_claims
        guard.audit_answer = audit_answer
        policy = types.ModuleType("src.policy_claim_guard")
        policy.audit_policy_claims = audit_policy_claims
        modules = patch.dict(sys.modules, {"src.guardrail": guard,
                                           "src.policy_claim_guard": policy})
        modules.start()
        self.addCleanup(modules.stop)
        dispatch = patch.dict(agent.ALL_TOOL_DISPATCH, {
            "get_history": lambda account_id: copy.deepcopy(HISTORY),
            "get_latest_score": lambda account_id: copy.deepcopy(HISTORY[-1]),
            "retrieve_policy": lambda query, k=3: [
                {"source": "POL-002.md", "heading": "Early warning", "text": "Example"}
            ],
        })
        dispatch.start()
        self.addCleanup(dispatch.stop)

    def call(self, *replies, max_tool_rounds=8):
        llm = ScriptedLLM(*replies)
        result = agent.run_agent("Evaluate account 81000001", llm,
                                 max_tool_rounds=max_tool_rounds, verbose=False)
        return result, llm

    @staticmethod
    def history_call():
        return Reply(tool_calls=[ToolCall("h1", "get_history", {"account_id": 81000001})])

    @staticmethod
    def policy_call():
        return Reply(tool_calls=[ToolCall("p1", "retrieve_policy", {"query": "warning signal"})])

    def test_verified_narrative_released_after_two_tool_rounds(self):
        result, llm = self.call(self.history_call(), self.policy_call(),
                                Reply(text=json.dumps(PAYLOAD)))
        self.assertTrue(result["publishable"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["next_action"], "publish")
        self.assertEqual(result["answer"], PAYLOAD["answer"])
        self.assertEqual(result["tool_rounds"], 2)
        self.assertEqual(len(result["tool_evidence"]), 2)
        self.assertEqual(llm.rounds, 2)
        self.assertIn("score_claims", llm.initial_messages[0]["content"])
        self.assertEqual(len(result["verification"]["numeric"]["checks"]), 1)
        self.assertEqual(len(result["verification"]["policy"]["checks"]), 1)

    def test_parallel_tools_supported(self):
        parallel = Reply(tool_calls=[
            ToolCall("h1", "get_history", {"account_id": 81000001}),
            ToolCall("p1", "retrieve_policy", {"query": "warning signal"}),
        ])
        result, llm = self.call(parallel, Reply(text=json.dumps(PAYLOAD)))
        self.assertTrue(result["publishable"])
        self.assertEqual(result["tool_rounds"], 1)
        self.assertEqual(len(llm.results_seen), 2)

    def test_policy_claim_without_retrieved_memo_is_not_released(self):
        result, _ = self.call(self.history_call(), Reply(text=json.dumps(PAYLOAD)))
        self.assertFalse(result["publishable"])
        self.assertEqual(result["reason_codes"], ["POLICY_RETRIEVAL_MISSING"])
        self.assertEqual(result["next_action"], "gather_evidence")
        # The numerical and policy validators did pass; retrieval remains missing.
        self.assertEqual(result["verification"]["status"], "passed")

    def test_numeric_only_can_pass_without_policy_retrieval(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["policy_claims"] = []
        result, _ = self.call(self.history_call(), Reply(text=json.dumps(payload)))
        self.assertTrue(result["publishable"])

    def test_fabricated_score_is_blocked(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["answer"] = "Account 81000001 had a score of 999 on 2025-04-01 in market 1."
        payload["score_claims"][0]["value"] = 999
        result, _ = self.call(self.history_call(), Reply(text=json.dumps(payload)))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["publishable"])
        self.assertEqual(result["next_action"], "revise_response")
        self.assertNotIn("999", result["answer"])

    def test_false_policy_is_blocked(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["policy_claims"][0]["value"] = "not_triggered"
        result, _ = self.call(self.history_call(), Reply(text=json.dumps(payload)))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["publishable"])

    def test_invalid_json_proposes_regeneration(self):
        result, _ = self.call(self.history_call(), Reply(text="This is not JSON"))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["next_action"], "regenerate_json")
        self.assertNotIn("This is not JSON", result["answer"])

    def test_unchecked_narrative_remains_blocked(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["answer"] += " There was an unparsed 80-point decline."
        result, _ = self.call(self.history_call(), Reply(text=json.dumps(payload)))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["publishable"])

    def test_missing_explicit_claim_blocks(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["score_claims"] = []
        result, _ = self.call(self.history_call(), Reply(text=json.dumps(payload)))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["publishable"])

    def test_missing_history_requests_evidence(self):
        latest = Reply(tool_calls=[ToolCall("l1", "get_latest_score",
                                            {"account_id": 81000001})])
        # Avoid a prose score claim: the legacy scanner can classify an
        # unevidenced prose number as failed rather than incomplete.
        payload = copy.deepcopy(PAYLOAD)
        payload["answer"] = "The requested account requires an evidence check."
        result, _ = self.call(latest, Reply(text=json.dumps(payload)))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["publishable"])
        self.assertEqual(result["next_action"], "gather_evidence")

    def test_no_tool_calls_does_not_pretend_to_have_evidence(self):
        result, _ = self.call(Reply(text=json.dumps(PAYLOAD)))
        self.assertTrue(result["completed"])
        self.assertFalse(result["publishable"])
        self.assertEqual(result["tool_rounds"], 0)

    def test_unknown_tool_blocks_even_with_valid_json(self):
        unknown = Reply(tool_calls=[ToolCall("x1", "does_not_exist", {})])
        result, _ = self.call(unknown, Reply(text=json.dumps(PAYLOAD)))
        self.assertFalse(result["publishable"])
        self.assertEqual(result["reason_codes"], ["TOOL_EXECUTION_ERROR"])

    def test_invalid_tool_arguments_block(self):
        invalid = Reply(tool_calls=[ToolCall("h1", "get_history", {"unexpected": 81000001})])
        result, _ = self.call(invalid, Reply(text=json.dumps(PAYLOAD)))
        self.assertFalse(result["publishable"])
        self.assertIn("TOOL_EXECUTION_ERROR", result["reason_codes"])

    def test_tool_round_limit_is_safe(self):
        result = agent.run_agent("Question", InfiniteLLM(), max_tool_rounds=2,
                                 verbose=False)
        self.assertFalse(result["completed"])
        self.assertEqual(result["tool_rounds"], 2)
        self.assertEqual(len(result["tool_evidence"]), 2)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["reason_codes"], ["TOOL_ROUND_LIMIT"])
        self.assertIsNone(result["verification"])

    def test_verifier_exception_fails_closed(self):
        with patch.object(agent, "verify_structured_response", side_effect=RuntimeError("simulated")):
            result, _ = self.call(self.history_call(), Reply(text=json.dumps(PAYLOAD)))
        self.assertFalse(result["publishable"])
        self.assertEqual(result["reason_codes"], ["VERIFICATION_ERROR"])
        self.assertNotEqual(result["answer"], PAYLOAD["answer"])

    def test_empty_reply_is_not_completion_proof(self):
        result, _ = self.call(Reply(text=""))
        self.assertTrue(result["completed"])
        self.assertFalse(result["publishable"])
        self.assertEqual(result["next_action"], "regenerate_json")

    def test_validation_of_arguments(self):
        with self.assertRaises(ValueError):
            agent.run_agent("", ScriptedLLM(), verbose=False)
        with self.assertRaises(ValueError):
            agent.run_agent("hi", ScriptedLLM(), max_tool_rounds=0, verbose=False)

    def test_relevant_policy_period_is_not_future_leaked(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["policy_claims"][0]["as_of_period"] = "2025-03-01"
        payload["score_claims"] = [{"account_id": 81000001, "period": "2025-03-01",
                                    "metric": "score", "value": 267, "market": 1}]
        payload["answer"] = "Account 81000001 had a score of 267 on 2025-03-01 in market 1."
        result, _ = self.call(self.history_call(), self.policy_call(),
                              Reply(text=json.dumps(payload)))
        self.assertTrue(result["publishable"], result["verification"])

    def test_declaration_without_claims_is_not_verified(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["score_claims"] = []
        payload["policy_claims"] = []
        result, _ = self.call(self.history_call(), Reply(text=json.dumps(payload)))
        self.assertFalse(result["publishable"])
        self.assertEqual(result["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
