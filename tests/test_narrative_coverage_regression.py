"""Module 5.4.4 regression for the FIRST real-provider sentence.

Uses deterministic stand-ins for existing guardrail functions, because those
implementations are local to the project. Tests the real coordinator + gate.
"""
import copy
import json
import sys
import types
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from src.response_gate import handle_verification
from src.verification_pipeline import verify_structured_response

SENTENCE = (
    "For account 81000001 on 2025-04-01, the score is 271 and the risk "
    "bin is 6 in market 1, based on the account history retrieved."
)
CLAIMS = [
    {"account_id": 81000001, "period": "2025-04-01", "metric": "score", "value": 271, "market": 1},
    {"account_id": 81000001, "period": "2025-04-01", "metric": "bin", "value": 6, "market": 1},
]
EVIDENCE = [{
    "tool_name": "get_history",
    "arguments": {"account_id": 81000001},
    "result": [{"period": "2025-04-01", "score": 271, "bin": 6, "market": 1}],
}]


@dataclass(frozen=True)
class FakeScoreClaim:
    account_id: int
    period: str
    metric: str
    value: int
    market: int


def score_checker(claim, evidence):
    rows = [row for item in evidence if item.get("tool_name") == "get_history"
            for row in item["result"]]
    return {
        "value_grounded": any(r.get(claim.metric) == claim.value for r in rows),
        "context_grounded": any(
            item.get("arguments", {}).get("account_id") == claim.account_id
            and r.get("period") == claim.period
            and r.get("market") == claim.market
            and r.get(claim.metric) == claim.value
            for item in evidence if item.get("tool_name") == "get_history"
            for r in item["result"]
        ),
    }


def legacy_unparsed(answer, evidence):
    # Matches the exact diagnostic behavior observed in the live test.
    return {"checks": [], "coverage_complete": False, "status": "incomplete",
            "unchecked_text": [answer]}


class NarrativeCoverageRegression(unittest.TestCase):
    def setUp(self):
        guard = types.ModuleType("src.guardrail")
        guard.ScoreClaim = FakeScoreClaim
        guard.check_score_claim = score_checker
        guard.extract_score_claims = lambda answer: []
        guard.audit_answer = legacy_unparsed
        policy = types.ModuleType("src.policy_claim_guard")
        policy.audit_policy_claims = lambda *args, **kwargs: {
            "status": "passed", "checks": [{"passed": True}]
        }
        module_patch = patch.dict(sys.modules, {
            "src.guardrail": guard, "src.policy_claim_guard": policy
        })
        module_patch.start()
        self.addCleanup(module_patch.stop)

    def payload(self):
        return {"answer": SENTENCE, "score_claims": copy.deepcopy(CLAIMS),
                "policy_claims": [], "limitations": []}

    def verify(self, payload=None, evidence=None):
        return verify_structured_response(
            json.dumps(self.payload() if payload is None else payload),
            EVIDENCE if evidence is None else evidence,
        )

    def test_exact_live_sentence_is_publishable(self):
        audit = self.verify()
        result = handle_verification(audit)
        self.assertEqual(audit["status"], "passed", audit)
        self.assertEqual(audit["narrative"]["coverage_method"], "strict_score_bin_sentence_v2")
        self.assertEqual(audit["narrative"]["unchecked_text"], [])
        self.assertEqual(len(audit["narrative"]["checks"]), 2)
        self.assertTrue(result["publishable"])
        self.assertEqual(result["public_answer"], SENTENCE)

    def test_missing_bin_claim_stays_incomplete(self):
        data = self.payload()
        data["score_claims"] = data["score_claims"][:1]
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_extra_sentence_remains_blocked(self):
        data = self.payload()
        data["answer"] += " The score therefore guarantees repayment."
        self.assertFalse(handle_verification(self.verify(data))["publishable"])

    def test_extra_number_remains_blocked(self):
        data = self.payload()
        data["answer"] += " It declined by 80 points."
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_wrong_score_in_narrative_remains_blocked(self):
        data = self.payload()
        data["answer"] = SENTENCE.replace("score is 271", "score is 275")
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_wrong_bin_in_narrative_remains_blocked(self):
        data = self.payload()
        data["answer"] = SENTENCE.replace("risk bin is 6", "risk bin is 7")
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_wrong_account_in_narrative_remains_blocked(self):
        data = self.payload()
        data["answer"] = SENTENCE.replace("account 81000001", "account 81000999")
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_fabricated_structured_score_fails(self):
        data = self.payload()
        data["score_claims"][0]["value"] = 999
        self.assertEqual(self.verify(data)["status"], "failed")

    def test_missing_history_is_not_upgraded(self):
        audit = self.verify(evidence=[])
        self.assertEqual(audit["status"], "incomplete")
        self.assertFalse(handle_verification(audit)["publishable"])

    def test_policy_claim_does_not_use_numerical_exception(self):
        data = self.payload()
        data["policy_claims"] = [
            {"field": "warning_signal_status", "value": "triggered",
             "account_id": 81000001, "as_of_period": "2025-04-01", "market": 1}
        ]
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_original_legacy_failure_is_not_overridden(self):
        fake_guardrail = sys.modules["src.guardrail"]

        def failed_audit(answer, evidence):
            return {
                "status": "failed",
                "coverage_complete": False,
                "checks": [],
                "unchecked_text": [answer],
            }

        with patch.object(
                fake_guardrail,
                "audit_answer",
                side_effect=failed_audit,
        ):
            self.assertEqual(
                self.verify()["status"],
                "failed",
            )

    def test_agent_releases_valid_exact_live_sentence(self):
        from src import agent

        @dataclass
        class ToolCall:
            id: str
            name: str
            arguments: dict

        @dataclass
        class Reply:
            text: str = ""
            tool_calls: list | None = None

            def __post_init__(self):
                if self.tool_calls is None:
                    self.tool_calls = []

        class ScriptedLLM:
            def chat(self, messages, tools=None):
                return Reply(tool_calls=[ToolCall("h1", "get_history", {"account_id": 81000001})])

            def continue_with_tool_results(self, assistant_response, tool_results, tools=None):
                assert tool_results[0] == EVIDENCE[0]["result"]
                return Reply(text=json.dumps(self_payload))

        self_payload = self.payload()
        with patch.dict(agent.ALL_TOOL_DISPATCH, {
            "get_history": lambda account_id: copy.deepcopy(EVIDENCE[0]["result"])
        }):
            result = agent.run_agent("Check score and bin", ScriptedLLM(), verbose=False)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["publishable"])
        self.assertEqual(result["answer"], SENTENCE)
        self.assertEqual(result["tool_rounds"], 1)


    def test_second_live_sentence_past_tense_without_suffix_is_publishable(self):
        data = self.payload()
        data["answer"] = (
            "For account 81000001 on 2025-04-01, the score was 271 "
            "and the risk bin was 6 in market 1."
        )
        audit = self.verify(data)
        decision = handle_verification(audit)
        self.assertEqual(audit["status"], "passed", audit)
        self.assertEqual(audit["narrative"]["coverage_method"], "strict_score_bin_sentence_v2")
        self.assertEqual(audit["narrative"]["unchecked_text"], [])
        self.assertTrue(decision["publishable"])
        self.assertEqual(decision["public_answer"], data["answer"])

    def test_second_live_sentence_past_tense_with_suffix_is_publishable(self):
        data = self.payload()
        data["answer"] = (
            "For account 81000001 on 2025-04-01, the score was 271 "
            "and the risk bin was 6 in market 1, based on the account history retrieved."
        )
        self.assertEqual(self.verify(data)["status"], "passed")

    def test_present_tense_without_suffix_is_publishable(self):
        data = self.payload()
        data["answer"] = (
            "For account 81000001 on 2025-04-01, the score is 271 "
            "and the risk bin is 6 in market 1."
        )
        self.assertEqual(self.verify(data)["status"], "passed")

    def test_second_live_sentence_wrong_bin_stays_blocked(self):
        data = self.payload()
        data["answer"] = (
            "For account 81000001 on 2025-04-01, the score was 271 "
            "and the risk bin was 7 in market 1."
        )
        self.assertFalse(handle_verification(self.verify(data))["publishable"])

    def test_second_live_sentence_extra_statement_stays_blocked(self):
        data = self.payload()
        data["answer"] = (
            "For account 81000001 on 2025-04-01, the score was 271 "
            "and the risk bin was 6 in market 1. Credit can be automatically approved."
        )
        self.assertEqual(self.verify(data)["status"], "incomplete")

    def test_second_live_sentence_missing_bin_claim_stays_blocked(self):
        data = self.payload()
        data["answer"] = (
            "For account 81000001 on 2025-04-01, the score was 271 "
            "and the risk bin was 6 in market 1."
        )
        data["score_claims"] = data["score_claims"][:1]
        self.assertFalse(handle_verification(self.verify(data))["publishable"])

    def test_mixed_tenses_not_silently_broadened(self):
        data = self.payload()
        data["answer"] = (
            "For account 81000001 on 2025-04-01, the score was 271 "
            "and the risk bin is 6 in market 1."
        )
        self.assertEqual(self.verify(data)["status"], "incomplete")


    def test_agent_releases_second_live_sentence(self):
        from src import agent

        @dataclass
        class ToolCall:
            id: str
            name: str
            arguments: dict

        @dataclass
        class Reply:
            text: str = ""
            tool_calls: list | None = None

            def __post_init__(self):
                if self.tool_calls is None:
                    self.tool_calls = []

        narrative = (
            "For account 81000001 on 2025-04-01, the score was 271 "
            "and the risk bin was 6 in market 1."
        )
        payload = self.payload()
        payload["answer"] = narrative

        class ScriptedLLM:
            def chat(self, messages, tools=None):
                return Reply(tool_calls=[ToolCall("h1", "get_history", {"account_id": 81000001})])

            def continue_with_tool_results(self, assistant_response, tool_results, tools=None):
                assert tool_results[0] == EVIDENCE[0]["result"]
                return Reply(text=json.dumps(payload))

        with patch.dict(agent.ALL_TOOL_DISPATCH, {
            "get_history": lambda account_id: copy.deepcopy(EVIDENCE[0]["result"])
        }):
            result = agent.run_agent("Check score and bin", ScriptedLLM(), verbose=False)

        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["publishable"])
        self.assertEqual(result["answer"], narrative)
        self.assertEqual(result["tool_rounds"], 1)

    def test_different_unparsed_format_remains_incomplete(self):
        data = self.payload()
        data["answer"] = (
            "Account 81000001 scored 271 on April 1, 2025 and was in bin 6."
        )
        self.assertEqual(self.verify(data)["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
