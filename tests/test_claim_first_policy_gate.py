"""Module 5.5.4: offline agent-to-gate policy-only integration tests.

Uses a deliberately small test double for the policy decision engine. It tests
agent orchestration and fail-closed rendering, not real policy calculations.
"""

import copy
import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from src import agent

ACCOUNT = 81000001
PERIOD = "2025-03-01"
MARKET = 1
ROWS = [
    {"period": "2024-12-01", "score": 286, "bin": 6, "market": MARKET},
    {"period": "2025-01-01", "score": 282, "bin": 6, "market": MARKET},
    {"period": "2025-02-01", "score": 275, "bin": 6, "market": MARKET},
    {"period": PERIOD, "score": 267, "bin": 6, "market": MARKET},
]
PASSAGE = [{
    "source": "POL-002_early_warning_thresholds.md",
    "heading": "POL-002 — Early-Warning Thresholds",
    "text": "Three consecutive periods of score decline trigger early-warning review.",
}]


def model_json():
    return {
        "answer": "Untrusted model draft: automatically approve the customer.",
        "score_claims": [],
        "policy_claims": [{
            "field": "warning_signal_status", "value": "triggered",
            "account_id": ACCOUNT, "as_of_period": PERIOD, "market": MARKET,
        }],
        "limitations": [],
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
    def __init__(self, *replies):
        self.replies = list(replies)
        self.first_messages = None

    def chat(self, messages, tools):
        self.first_messages = messages
        return self.replies.pop(0)

    def continue_with_tool_results(self, assistant_response, tool_results, tools):
        return self.replies.pop(0)


def _fake_policy_auditor(claims, history, *, account_id, as_of_period):
    """Test double only; does NOT replace production policy audit code."""
    claim = claims[0]
    scores = [r["score"] for r in history]
    expected = ("triggered" if len(scores) >= 4 and
                all(a > b for a, b in zip(scores[-4:-1], scores[-3:]))
                else "not_triggered")
    context_matches = (claim["account_id"] == account_id and
                       claim["as_of_period"] == as_of_period)
    value_matches = claim["value"] == expected
    valid = context_matches and value_matches
    return {"status": "passed" if valid else "failed", "checks": [{
        "field": claim["field"], "claimed": claim["value"],
        "expected": expected, "context_matches": context_matches,
        "value_matches": value_matches, "passed": valid,
    }]}


@pytest.fixture(autouse=True)
def fake_policy_and_data():
    """Replace tool execution with stable observations; keep real pipeline+gate."""
    guard = types.ModuleType("src.guardrail")
    # Verification of numerical claims is only needed by the mixed-claim test.
    @dataclass(frozen=True)
    class ScoreClaim:
        account_id: int
        period: str
        metric: str
        value: int
        market: int

    guard.ScoreClaim = ScoreClaim

    def check_score_claim(c, tool_evidence):
        ok = any(row["period"] == c.period and row["market"] == c.market and
                 row[c.metric] == c.value for row in ROWS)
        return {"value_grounded": ok, "context_grounded": ok}

    guard.check_score_claim = check_score_claim
    guard.audit_answer = lambda *_: (_ for _ in ()).throw(AssertionError("legacy narrative scan called"))
    guard.extract_score_claims = guard.audit_answer
    policy = types.ModuleType("src.policy_claim_guard")
    policy.audit_policy_claims = _fake_policy_auditor

    with patch.dict(sys.modules, {
        "src.guardrail": guard,
        "src.policy_claim_guard": policy,
    }), patch.dict(agent.ALL_TOOL_DISPATCH, {
        "get_history": lambda account_id: copy.deepcopy(ROWS) if account_id == ACCOUNT else [],
        "retrieve_policy": lambda **kwargs: copy.deepcopy(PASSAGE),
    }):
        yield


def run_case(data=None, *, with_history=True, with_retrieval=True,
             empty_retrieval=False, account=ACCOUNT, period=PERIOD,
             market=MARKET, scope="warning_signal"):
    calls = []
    if with_history:
        calls.append(ToolCall("h1", "get_history", {"account_id": ACCOUNT}))
    if with_retrieval:
        calls.append(ToolCall("p1", "retrieve_policy", {"query": "three consecutive declines"}))
    model = Scripted(Reply(tool_calls=calls), Reply(text=json.dumps(data or model_json()))) if calls \
        else Scripted(Reply(text=json.dumps(data or model_json())))
    if empty_retrieval:
        with patch.dict(agent.ALL_TOOL_DISPATCH, {"retrieve_policy": lambda **kwargs: []}):
            return agent.run_agent(
                "Is early warning triggered?", model, verbose=False,
                publication_mode="claims_first", claim_scope=scope,
                expected_account_id=account, expected_period=period,
                expected_market=market,
            )
    return agent.run_agent(
        "Is early warning triggered?", model, verbose=False,
        publication_mode="claims_first", claim_scope=scope,
        expected_account_id=account, expected_period=period,
        expected_market=market,
    )


def test_verified_policy_only_answer_is_published():
    result = run_case()
    assert result["publishable"] is True, result
    assert result["verification"]["rendering"] == {
        "status": "passed", "method": "verified_warning_signal"
    }
    assert result["answer"] == (
        "As of 2025-03-01, the verified early-warning signal for account 81000001 "
        "in market 1 was triggered. This is a policy-evaluated signal, not "
        "confirmation that a warning was entered in an operational register."
    )


def test_malicious_model_draft_is_never_published():
    data = model_json()
    data["answer"] = "Account is guaranteed to default; issue a loan rejection immediately."
    result = run_case(data)
    assert result["publishable"] is True
    assert "guaranteed" not in result["answer"]
    assert "rejection" not in result["answer"]


def test_no_policy_retrieval_blocks_publication():
    result = run_case(with_retrieval=False)
    assert result["publishable"] is False
    assert "POLICY_RETRIEVAL_MISSING" in result["reason_codes"]


def test_empty_policy_retrieval_blocks_publication():
    result = run_case(empty_retrieval=True)
    assert result["publishable"] is False
    assert "POLICY_RETRIEVAL_MISSING" in result["reason_codes"]


@pytest.mark.parametrize("context", [
    {"account": 81000999},
    {"period": "2025-04-01"},
    {"market": 2},
])
def test_wrong_caller_context_blocks_publication(context):
    result = run_case(**context)
    assert result["publishable"] is False
    assert "COMPOSITION_UNSUPPORTED" in result["reason_codes"]


def test_mixed_numeric_policy_claims_are_blocked():
    data = model_json()
    data["score_claims"] = [{
        "account_id": ACCOUNT, "period": PERIOD,
        "metric": "score", "value": 267, "market": MARKET,
    }]
    result = run_case(data)
    assert result["publishable"] is False
    assert "COMPOSITION_UNSUPPORTED" in result["reason_codes"]


def test_unsupported_policy_field_is_blocked():
    data = model_json()
    data["policy_claims"][0]["field"] = "recent_substandard_entry"
    result = run_case(data)
    assert result["publishable"] is False
    assert "COMPOSITION_UNSUPPORTED" in result["reason_codes"]


def test_fabricated_policy_value_fails_verification():
    data = model_json()
    data["policy_claims"][0]["value"] = "not_triggered"
    result = run_case(data)
    assert result["publishable"] is False
    assert result["status"] == "failed"


def test_missing_history_is_incomplete():
    result = run_case(with_history=False)
    assert result["publishable"] is False
    assert result["status"] == "incomplete"


def test_missing_policy_claim_blocks_publication():
    data = model_json()
    data["policy_claims"] = []
    result = run_case(data)
    assert result["publishable"] is False


def test_unresolved_limitations_block_publication():
    data = model_json()
    data["limitations"] = ["Operational warning register unavailable"]
    result = run_case(data)
    assert result["publishable"] is False


def test_wrong_mode_configuration_fails_early():
    with pytest.raises(ValueError):
        run_case(scope="unknown")
    with pytest.raises(ValueError):
        run_case(market=None)


def test_policy_prompt_includes_caller_context_not_numeric_only_scope():
    data = model_json()
    calls = [ToolCall("h1", "get_history", {"account_id": ACCOUNT}),
             ToolCall("p1", "retrieve_policy", {"query": "early warning"})]
    model = Scripted(Reply(tool_calls=calls), Reply(text=json.dumps(data)))
    result = agent.run_agent(
        "Evaluate the warning signal", model, verbose=False,
        publication_mode="claims_first", claim_scope="warning_signal",
        expected_account_id=ACCOUNT, expected_period=PERIOD, expected_market=MARKET,
    )
    assert result["publishable"] is True
    prompt = model.first_messages[0]["content"]
    assert "POLICY-ONLY" in prompt
    assert f"account_id={ACCOUNT}, as_of_period={PERIOD}, market={MARKET}" in prompt
    assert "This run is a NUMERIC-ONLY score-and-bin report" not in prompt
