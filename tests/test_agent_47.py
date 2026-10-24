"""Updated Module 4.7 compatibility checks for the fail-closed Module 5.4.4 agent.

The old assertion 'answer == Final explanation' is intentionally obsolete:
plain text without verified JSON claims must no longer be publicly released.
"""
import unittest
from dataclasses import dataclass, field
from src.agent import run_agent


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Response:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class BadLoop:
    def chat(self, messages, tools):
        return Response(tool_calls=[ToolCall("1", "not_a_tool", {})])

    def continue_with_tool_results(self, assistant_response, tool_results, tools):
        assert tool_results[0]["error"].startswith("Unknown tool:")
        return Response(tool_calls=[ToolCall("1", "not_a_tool", {})])


class PlainText:
    def chat(self, messages, tools):
        return Response(text="Final explanation")


class AgentCompatibilityTests(unittest.TestCase):
    def test_old_plain_text_is_withheld(self):
        result = run_agent("Hello", PlainText(), verbose=False)
        self.assertTrue(result["completed"])
        self.assertEqual(result["tool_rounds"], 0)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["status"], "incomplete")
        self.assertNotEqual(result["answer"], "Final explanation")

    def test_round_limit_still_holds(self):
        result = run_agent("Loop test", BadLoop(), max_tool_rounds=2, verbose=False)
        self.assertFalse(result["completed"])
        self.assertEqual(result["tool_rounds"], 2)
        self.assertEqual(len(result["tool_evidence"]), 2)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["reason_codes"], ["TOOL_ROUND_LIMIT"])


if __name__ == "__main__":
    unittest.main()
