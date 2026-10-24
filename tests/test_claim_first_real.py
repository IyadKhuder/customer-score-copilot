"""Runs against the user's real SQLite score DB and guardrail; no LLM API calls."""
import json
import unittest
from dataclasses import dataclass, field

try:
    from src.agent import run_agent
    from src.guardrail import check_score_claim  # noqa: F401 - availability check
    from src.policy_claim_guard import audit_policy_claims  # noqa: F401
    from src.score_tools import get_history
except ImportError as exc:
    raise RuntimeError(f"Required validators unavailable: {exc}") from exc


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


class FixedLLM:
    def chat(self, messages, tools=None):
        return Reply(tool_calls=[ToolCall("h1", "get_history", {"account_id": 1000003})])

    def continue_with_tool_results(self, assistant_response, tool_results, tools=None):
        rows = tool_results[0]
        observed = next((row for row in rows if row["period"] == "1991-12-01"), None)
        if observed is None:
            raise AssertionError("Expected demo observation missing from the local DB")
        data = {
            "answer": "Different untrusted wording!",
            "score_claims": [
                {"account_id": 1000003, "period": "1991-12-01", "market": observed["market"],
                 "metric": metric, "value": observed[metric]}
                for metric in ("score", "bin")
            ],
            "policy_claims": [],
            "limitations": [],
        }
        return Reply(text=json.dumps(data))


class RealClaimFirstSmokeTest(unittest.TestCase):
    def test_real_validators_and_database(self):
        try:
            rows = get_history(1000003)
        except Exception as exc:
            self.fail(f"Score database unavailable: {exc}")
        if not rows:
            self.fail("Score database lacks account 1000003")
        result = run_agent(
            "Report score and bin on 1991-12-01 for account 1000003",
            FixedLLM(), verbose=False, publication_mode="claims_first",
            expected_account_id=1000003, expected_period="1991-12-01",
        )
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(result["publishable"])
        self.assertIn("score was 265", result["answer"])
        self.assertIn("bin was 6", result["answer"])
        self.assertNotIn("Different untrusted wording", result["answer"])


if __name__ == "__main__":
    unittest.main()
