"""Module 5.5.5 combined-claim renderer and offline agent/gate tests.

Policy decisions and score-tool outputs are deterministic test doubles. The
production verification pipeline, composers, and publication path are used.
"""

import copy
import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from src import agent
from src.narrative_composer import (
    CompositionError, compose_verified_score_bin_warning_signal,
)

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


def payload():
    return {
        "answer": "UNTRUSTED: automatically deny the customer's credit!",
        "score_claims": [
            {"account_id": ACCOUNT, "period": PERIOD, "metric": "score", "value": 267, "market": MARKET},
            {"account_id": ACCOUNT, "period": PERIOD, "metric": "bin", "value": 6, "market": MARKET},
        ],
        "policy_claims": [
            {"field": "warning_signal_status", "value": "triggered",
             "account_id": ACCOUNT, "as_of_period": PERIOD, "market": MARKET},
        ],
        "limitations": [],
    }


def passing_audit():
    data = payload()
    return {
        "status": "passed", "schema": {"status": "passed"},
        "issues": [], "limitations": [], "answer": data["answer"],
        "narrative": {"coverage_method": "llm_draft_excluded"},
        "numeric": {"status": "passed", "checks": [
            {"status": "passed", "value_grounded": True,
             "context_grounded": True, "claim": c}
            for c in data["score_claims"]
        ]},
        "policy": {"status": "passed", "checks": [{
            "status": "passed", "claim": data["policy_claims"][0],
            "audit": {"status": "passed", "checks": [{
                "field": "warning_signal_status", "claimed": "triggered",
                "expected": "triggered", "context_matches": True,
                "value_matches": True, "passed": True,
            }]},
        }]},
    }


def render(audit=None, **context):
    return compose_verified_score_bin_warning_signal(
        audit if audit is not None else passing_audit(),
        **(context or {"account_id": ACCOUNT, "period": PERIOD, "market": MARKET}),
    )


def test_combined_composer_renders_all_three_supported_claims():
    answer = render()
    assert answer == (
        "For account 81000001 on 2025-03-01, the verified score was 267 "
        "and the risk bin was 6 in market 1.\n\n"
        "As of 2025-03-01, the verified early-warning signal for account "
        "81000001 in market 1 was triggered. This is a policy-evaluated "
        "signal, not confirmation that a warning was entered in an operational register."
    )
    assert "automatically deny" not in answer


@pytest.mark.parametrize("edit", [
    lambda a: a.update(status="failed"),
    lambda a: a["schema"].update(status="incomplete"),
    lambda a: a["numeric"].update(status="incomplete"),
    lambda a: a["policy"].update(status="failed"),
    lambda a: a["narrative"].update(coverage_method="legacy_scan"),
    lambda a: a["limitations"].append("Unknown operational register"),
    lambda a: a["issues"].append("Audit inconsistent"),
])
def test_combined_composer_fail_closed_on_audit_gaps(edit):
    audit = passing_audit()
    edit(audit)
    with pytest.raises(CompositionError):
        render(audit)


@pytest.mark.parametrize("change", [
    lambda a: a["numeric"]["checks"].pop(),
    lambda a: a["numeric"]["checks"].append(copy.deepcopy(a["numeric"]["checks"][0])),
    lambda a: a["policy"]["checks"].clear(),
    lambda a: a["policy"]["checks"].append(copy.deepcopy(a["policy"]["checks"][0])),
    lambda a: a["numeric"]["checks"][0].update(context_grounded=False),
    lambda a: a["policy"]["checks"][0]["claim"].update(field="recent_substandard_entry"),
    lambda a: a["policy"]["checks"][0]["audit"]["checks"][0].update(value_matches=False),
])
def test_combined_composer_rejects_missing_extra_or_false_claims(change):
    audit = passing_audit()
    change(audit)
    with pytest.raises(CompositionError):
        render(audit)


@pytest.mark.parametrize("ctx", [
    {"account_id": 999999, "period": PERIOD, "market": MARKET},
    {"account_id": ACCOUNT, "period": "2025-04-01", "market": MARKET},
    {"account_id": ACCOUNT, "period": PERIOD, "market": 2},
    {"account_id": ACCOUNT, "period": PERIOD, "market": "1"},
])
def test_combined_composer_rejects_wrong_caller_context(ctx):
    with pytest.raises(CompositionError):
        render(**ctx)


def test_combined_composer_rejects_numeric_market_mismatch_with_policy():
    audit = passing_audit()
    for check in audit["numeric"]["checks"]:
        check["claim"]["market"] = 2
    with pytest.raises(CompositionError):
        render(audit)


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


def fake_policy_auditor(claims, history, *, account_id, as_of_period):
    claim = claims[0]
    scores = [r["score"] for r in history]
    expected = "triggered" if len(scores) >= 4 and all(
        a > b for a, b in zip(scores[-4:-1], scores[-3:])
    ) else "not_triggered"
    context_ok = claim["account_id"] == account_id and claim["as_of_period"] == as_of_period
    value_ok = claim["value"] == expected
    ok = context_ok and value_ok
    return {"status": "passed" if ok else "failed", "checks": [{
        "field": claim["field"], "claimed": claim["value"], "expected": expected,
        "context_matches": context_ok, "value_matches": value_ok, "passed": ok,
    }]}


@pytest.fixture(autouse=True)
def fake_tools():
    guard = types.ModuleType("src.guardrail")

    @dataclass(frozen=True)
    class ScoreClaim:
        account_id: int
        period: str
        metric: str
        value: int
        market: int

    guard.ScoreClaim = ScoreClaim

    def grounded(claim, tool_evidence):
        matches = [row for item in tool_evidence if item["tool_name"] == "get_history"
                   and item["arguments"].get("account_id") == claim.account_id
                   for row in item["result"]]
        correct = any(row["period"] == claim.period and row["market"] == claim.market
                      and row[claim.metric] == claim.value for row in matches)
        return {"value_grounded": correct, "context_grounded": correct}

    guard.check_score_claim = grounded
    guard.audit_answer = guard.extract_score_claims = lambda *_: (
        _ for _ in ()
    ).throw(AssertionError("legacy prose audited in claim-first mode"))
    policy = types.ModuleType("src.policy_claim_guard")
    policy.audit_policy_claims = fake_policy_auditor

    with patch.dict(sys.modules, {"src.guardrail": guard, "src.policy_claim_guard": policy}), \
         patch.dict(agent.ALL_TOOL_DISPATCH, {
             "get_history": lambda account_id: copy.deepcopy(ROWS) if account_id == ACCOUNT else [],
             "retrieve_policy": lambda **kwargs: copy.deepcopy(PASSAGE),
         }):
        yield


def agent_case(data=None, *, history=True, retrieval=True, empty_retrieval=False,
               account=ACCOUNT, period=PERIOD, market=MARKET):
    calls = []
    if history:
        calls.append(ToolCall("h1", "get_history", {"account_id": ACCOUNT}))
    if retrieval:
        calls.append(ToolCall("p1", "retrieve_policy", {"query": "three declines"}))
    model = Scripted(Reply(tool_calls=calls), Reply(text=json.dumps(data if data is not None else payload()))) \
        if calls else Scripted(Reply(text=json.dumps(data if data is not None else payload())))

    if empty_retrieval:
        with patch.dict(agent.ALL_TOOL_DISPATCH, {"retrieve_policy": lambda **kwargs: []}):
            result = agent.run_agent(
                "Score, bin, and warning status", model, verbose=False,
                publication_mode="claims_first", claim_scope="score_bin_warning_signal",
                expected_account_id=account, expected_period=period, expected_market=market,
            )
    else:
        result = agent.run_agent(
            "Score, bin, and warning status", model, verbose=False,
            publication_mode="claims_first", claim_scope="score_bin_warning_signal",
            expected_account_id=account, expected_period=period, expected_market=market,
        )
    return result, model


def test_agent_publishes_verified_combined_answer():
    result, model = agent_case()
    assert result["publishable"] is True, result
    assert result["status"] == "passed"
    assert result["verification"]["rendering"] == {
        "status": "passed", "method": "verified_score_bin_warning_signal",
    }
    assert "the verified score was 267" in result["answer"]
    assert "risk bin was 6" in result["answer"]
    assert "early-warning signal" in result["answer"]
    assert "automatically deny" not in result["answer"]
    prompt = model.first_messages[0]["content"]
    assert "COMBINED score, bin" in prompt
    assert f"account_id={ACCOUNT}, as_of_period={PERIOD}, market={MARKET}" in prompt


@pytest.mark.parametrize("mutation", [
    lambda d: d["score_claims"].pop(),
    lambda d: d["policy_claims"].clear(),
    lambda d: d["score_claims"][0].update(value=999),
    lambda d: d["policy_claims"][0].update(value="not_triggered"),
    lambda d: d["score_claims"][0].update(market=2),
    lambda d: d["policy_claims"][0].update(market=2),
    lambda d: d["policy_claims"][0].update(field="recent_substandard_entry"),
    lambda d: d["limitations"].append("Missing warning-register data"),
])
def test_agent_blocks_missing_fabricated_or_unsupported_claims(mutation):
    data = payload()
    mutation(data)
    result, _ = agent_case(data)
    assert result["publishable"] is False, result
    assert "automatically deny" not in result["answer"]


@pytest.mark.parametrize("kwargs", [
    {"retrieval": False},
    {"empty_retrieval": True},
    {"history": False},
    {"account": 81000999},
    {"period": "2025-04-01"},
    {"market": 2},
])
def test_agent_blocks_missing_evidence_or_wrong_context(kwargs):
    result, _ = agent_case(**kwargs)
    assert result["publishable"] is False, result


def test_agent_rejects_missing_expected_market_before_tools():
    with pytest.raises(ValueError):
        agent.run_agent(
            "x", Scripted(Reply(text=json.dumps(payload()))), verbose=False,
            publication_mode="claims_first", claim_scope="score_bin_warning_signal",
            expected_account_id=ACCOUNT, expected_period=PERIOD,
        )


def test_agent_blocks_extra_policy_claim_instead_of_omitting_it():
    data = payload()
    data["policy_claims"].append(copy.deepcopy(data["policy_claims"][0]))
    result, _ = agent_case(data)
    assert result["publishable"] is False
    assert "COMPOSITION_UNSUPPORTED" in result["reason_codes"]
