"""Claim-first end-to-end tests: no live model, network, or database required."""
import copy
import json
import sys
import types
import unittest
from dataclasses import dataclass, field
from unittest.mock import patch

from src import agent
from src.narrative_composer import CompositionError, compose_verified_score_bin


ACCOUNT = 81000001
PERIOD = "2025-04-01"
ROWS = [{"period": PERIOD, "score": 271, "bin": 6, "market": 1}]


def payload():
    return {
        "answer": "The score was 271, and credit must automatically be approved!",
        "score_claims": [
            {"account_id": ACCOUNT, "period": PERIOD, "metric": "score", "value": 271, "market": 1},
            {"account_id": ACCOUNT, "period": PERIOD, "metric": "bin", "value": 6, "market": 1},
        ],
        "policy_claims": [],
        "limitations": [],
    }


@dataclass(frozen=True)
class ScoreClaim:
    account_id: int
    period: str
    metric: str
    value: int
    market: int


def grounded(claim, tool_evidence):
    rows = [row for entry in tool_evidence if entry["tool_name"] == "get_history"
            and entry["arguments"].get("account_id") == claim.account_id
            for row in entry["result"]]
    return {
        "value_grounded": any(row[claim.metric] == claim.value for row in rows),
        "context_grounded": any(row[claim.metric] == claim.value and row["period"] == claim.period
                                and row["market"] == claim.market for row in rows),
    }


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class Scripted:
    def __init__(self, *responses):
        self.responses = list(responses)

    def chat(self, messages, tools):
        return self.responses.pop(0)

    def continue_with_tool_results(self, assistant_response, tool_results, tools):
        return self.responses.pop(0)


class ClaimFirstTests(unittest.TestCase):
    def setUp(self):
        guard = types.ModuleType("src.guardrail")
        guard.ScoreClaim = ScoreClaim
        guard.check_score_claim = grounded
        # This MUST NOT be invoked in claim-first mode.
        guard.extract_score_claims = lambda *_: (_ for _ in ()).throw(AssertionError("legacy extractor called"))
        guard.audit_answer = lambda *_: (_ for _ in ()).throw(AssertionError("legacy auditor called"))
        policy = types.ModuleType("src.policy_claim_guard")
        policy.audit_policy_claims = lambda *args, **kwargs: {
            "status": "passed", "checks": [{"passed": True}],
        }
        mockmods = patch.dict(sys.modules, {
            "src.guardrail": guard, "src.policy_claim_guard": policy,
        })
        mockmods.start()
        self.addCleanup(mockmods.stop)
        dispatch = patch.dict(agent.ALL_TOOL_DISPATCH, {
            "get_history": lambda account_id: copy.deepcopy(ROWS),
        })
        dispatch.start()
        self.addCleanup(dispatch.stop)

    def run_case(self, data=None, *, get_history=True, account=ACCOUNT, period=PERIOD):
        raw = json.dumps(payload() if data is None else data)
        replies = ([Reply(tool_calls=[ToolCall("h1", "get_history", {"account_id": ACCOUNT})])]
                   if get_history else []) + [Reply(text=raw)]
        return agent.run_agent(
            "Account 81000001 score and bin on 2025-04-01", Scripted(*replies),
            publication_mode="claims_first", expected_account_id=account,
            expected_period=period, verbose=False,
        )

    def test_live_narrative_variation_is_ignored(self):
        result = self.run_case()
        self.assertTrue(result["publishable"], result)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["next_action"], "publish")
        self.assertEqual(result["answer"],
                         "For account 81000001 on 2025-04-01, the verified score was 271 and the risk bin was 6 in market 1.")
        self.assertNotIn("automatically", result["answer"])
        self.assertEqual(result["verification"]["narrative"]["status"], "not_applicable")
        self.assertEqual(result["verification"]["rendering"]["status"], "passed")

    def test_claim_order_does_not_matter(self):
        data = payload()
        data["score_claims"].reverse()
        self.assertTrue(self.run_case(data)["publishable"])

    def test_any_free_form_narrative_remains_unpublished(self):
        data = payload()
        data["answer"] = "This false analysis asserts a huge loss of 20000 points."
        result = self.run_case(data)
        self.assertTrue(result["publishable"])
        self.assertNotIn("20000", result["answer"])

    def test_fabricated_score_is_rejected(self):
        data = payload()
        data["score_claims"][0]["value"] = 999
        result = self.run_case(data)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("999", result["answer"])

    def test_missing_bin_is_rejected(self):
        data = payload()
        data["score_claims"] = data["score_claims"][:1]
        result = self.run_case(data)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["reason_codes"], ["COMPOSITION_UNSUPPORTED"])

    def test_policy_claims_are_not_rendered_or_silently_discarded(self):
        data = payload()
        data["policy_claims"] = [{"field":"warning_signal_status", "value":"triggered",
                                  "account_id":ACCOUNT,"as_of_period":PERIOD,"market":1}]
        result = self.run_case(data)
        self.assertFalse(result["publishable"])
        self.assertIn("COMPOSITION_UNSUPPORTED", result["reason_codes"])

    def test_limitations_require_broader_composer(self):
        data = payload()
        data["limitations"] = ["Warning register unavailable"]
        self.assertFalse(self.run_case(data)["publishable"])

    def test_wrong_expected_account_is_blocked(self):
        self.assertFalse(self.run_case(account=203480)["publishable"])

    def test_wrong_expected_period_is_blocked(self):
        self.assertFalse(self.run_case(period="2025-03-01")["publishable"])

    def test_missing_history_is_incomplete(self):
        result = self.run_case(get_history=False)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["next_action"], "gather_evidence")

    def test_missing_all_claims_is_incomplete(self):
        data = payload()
        data["score_claims"] = []
        result = self.run_case(data)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["status"], "incomplete")

    def test_extra_verified_numeric_claim_is_rejected(self):
        data = payload()
        data["score_claims"].append(copy.deepcopy(data["score_claims"][0]))
        self.assertFalse(self.run_case(data)["publishable"])

    def test_mismatched_market_claim_is_rejected(self):
        data = payload()
        data["score_claims"][1]["market"] = 2
        self.assertFalse(self.run_case(data)["publishable"])

    def test_invalid_context_fails_early(self):
        with self.assertRaises(ValueError):
            self.run_case(account=None)
        with self.assertRaises(ValueError):
            agent.run_agent("x", Scripted(Reply(text="{}")),
                            publication_mode="banana", verbose=False)

    def test_composer_rejects_non_iso_date(self):
        with self.assertRaises(CompositionError):
            compose_verified_score_bin({}, account_id=ACCOUNT, period="yesterday")

    def test_live_demo_past_tense_is_ignored(self):
        data = payload()
        data["answer"] = "For account 81000001 on 2025-04-01, the score was 271 and the risk bin was 6 in market 1."
        self.assertTrue(self.run_case(data)["publishable"])


if __name__ == "__main__":
    unittest.main()
