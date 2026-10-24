"""Module 5.5.7.4 — offline, scenario-based publication tests.

Exercises the ACTUAL policy engine, claim checker, verification pipeline, event
composers and agent dispatch. Only the provider, score-query backend, policy
retrieval backend, and the standalone numerical guardrail are controlled test
inputs. No API key, live LLM, network connection or production DB is used.

The project-wide suite separately tests the real numerical guardrail; this
scenario matrix focuses on cross-component behavior and publication safety.
"""

from __future__ import annotations

import copy
import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from src import agent
from src.policy_decisions import evaluate_policy_decision

ACCOUNT = 81000001
GRACE_ACCOUNT = 83000005
AS_OF = "2025-04-01"
ONBOARDED = "2024-05-01"
REVIEWED = "2025-04-01"
MONTHS = [
    "2024-09-01", "2024-10-01", "2024-11-01", "2024-12-01",
    "2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01",
]
DRAFT = "UNTRUSTED: automatic credit approval; all past warnings were cleared!"
DOCS = {
    "POL-002": {"source": "POL-002_early_warning_thresholds.md", "text": "Three consecutive declining months within the policy window."},
    "POL-004": {"source": "POL-004_site_market_exception_rules.md", "text": "Grace markets have adjusted warning thresholds."},
    "POL-005": {"source": "POL-005_data_quality_and_volatility_flags.md", "text": "Potential score artifacts require data-quality review."},
}


@dataclass(frozen=True)
class Scenario:
    name: str
    scope: str
    periods: tuple[str, ...]
    scores: tuple[int, ...]
    market: int
    warning: str
    count: int
    publishable: bool
    doc_ids: tuple[str, ...]
    phrase: str | None
    expected_reason: str | None = None

    @property
    def account(self) -> int:
        return GRACE_ACCOUNT if self.market == 3 else ACCOUNT

    def rows(self) -> list[dict]:
        return [
            {"period": period, "score": score, "bin": 6, "market": self.market}
            for period, score in zip(self.periods, self.scores)
        ]


SCENARIOS = (
    Scenario("standard_trigger", "score_bin_warning_signal_decline_events",
             tuple(MONTHS[-5:]), (286, 282, 275, 267, 271), 1,
             "triggered", 1, True, ("POL-002",), "eligible for early-warning consideration"),
    Scenario("standard_no_trigger", "score_bin_warning_signal_no_declines",
             tuple(MONTHS), (600, 602, 604, 606, 608, 610, 612, 614), 1,
             "not_triggered", 0, True, ("POL-002",), "was not triggered"),
    Scenario("standard_quality_hold", "score_bin_warning_signal_quality_hold",
             tuple(MONTHS[-5:]), (700, 530, 520, 510, 500), 1,
             "pending_data_quality", 2, True, ("POL-002", "POL-005"),
             "pending data-quality review"),
    Scenario("insufficient_history", "score_bin_warning_signal_quality_hold",
             tuple(MONTHS[-2:]), (610, 612), 1,
             "insufficient_evidence", 0, False, ("POL-002",), None,
             "POLICY_EVIDENCE_INSUFFICIENT"),
    Scenario("grace_eligible", "grace_decline_events",
             tuple(MONTHS), (700, 690, 680, 670, 660, 650, 651, 654), 3,
             "triggered", 1, True, ("POL-002", "POL-004"),
             "four consecutive monthly declines"),
    Scenario("grace_quality_hold", "grace_decline_events",
             tuple(MONTHS), (700, 690, 680, 670, 660, 650, 660, 660), 3,
             "pending_data_quality", 1, True, ("POL-002", "POL-004", "POL-005"),
             "pending data-quality review under POL-005"),
)
BY_NAME = {scenario.name: scenario for scenario in SCENARIOS}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class ScriptedProvider:
    """Fixed transcript of model tool calls; not an LLM reasoning benchmark."""

    def __init__(self, *replies: Reply):
        self.replies = list(replies)
        self.initial_messages = None

    def chat(self, *, messages, tools):
        self.initial_messages = messages
        return self.replies.pop(0)

    def continue_with_tool_results(self, *, assistant_response, tool_results, tools):
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def numeric_guardrail_double():
    """Supply ONLY the numeric guardrail adapter for offline integration tests.

    The real src.policy_claim_guard and src.policy_decisions are NOT replaced.
    """
    guard = types.ModuleType("src.guardrail")

    @dataclass(frozen=True)
    class ScoreClaim:
        account_id: int
        period: str
        metric: str
        value: int
        market: int

    def check_score_claim(claim, tool_evidence):
        rows = [row for entry in tool_evidence
                if entry.get("tool_name") == "get_history"
                and entry.get("arguments", {}).get("account_id") == claim.account_id
                and isinstance(entry.get("result"), list)
                for row in entry["result"]]
        value_ok = any(type(row.get(claim.metric)) is int
                       and row[claim.metric] == claim.value for row in rows)
        context_ok = any(row.get(claim.metric) == claim.value
                         and row.get("period") == claim.period
                         and row.get("market") == claim.market for row in rows)
        return {"value_grounded": value_ok, "context_grounded": context_ok}

    guard.ScoreClaim = ScoreClaim
    guard.check_score_claim = check_score_claim
    guard.extract_score_claims = lambda *_: (_ for _ in ()).throw(
        AssertionError("legacy extractor unexpectedly used"))
    guard.audit_answer = lambda *_: (_ for _ in ()).throw(
        AssertionError("legacy narrative auditor unexpectedly used"))
    with patch.dict(sys.modules, {"src.guardrail": guard}):
        yield


def claims(scenario: Scenario) -> dict:
    """Fixed expected claims: do NOT calculate their values via policy engine."""
    base = {"account_id": scenario.account, "as_of_period": AS_OF, "market": scenario.market}
    if scenario.market == 3:
        policy = [
            {**base, "field": "policy_applicability.mode", "value": "grace"},
            {**base, "field": "policy_applicability.thresholds.consecutive_declines", "value": 4},
            {**base, "field": "policy_applicability.quarterly_exception_review", "value": "current"},
            {**base, "field": "raw_event_count.consecutive_declines", "value": scenario.count},
        ]
        score = []
    else:
        policy = [
            {**base, "field": "warning_signal_status", "value": scenario.warning},
            {**base, "field": "raw_event_count.consecutive_declines", "value": scenario.count},
        ]
        score = [
            {"account_id": scenario.account, "period": AS_OF, "metric": metric,
             "value": value, "market": scenario.market}
            for metric, value in (("score", scenario.scores[-1]), ("bin", 6))
        ]
    return {"answer": DRAFT, "score_claims": score, "policy_claims": policy, "limitations": []}


def run_scenario(
    scenario: Scenario, *, rows=None, docs=None, payload=None,
    include_history=True, include_policy=True, expected_account=None,
    expected_period=AS_OF, expected_market=None, onboarded=ONBOARDED,
    reviewed=REVIEWED, additional_calls=(),
):
    """Run actual agent dispatch -> verification -> composer -> gate, offline."""
    history = scenario.rows() if rows is None else rows
    retrieval = [copy.deepcopy(DOCS[key]) for key in scenario.doc_ids] if docs is None else docs
    message = copy.deepcopy(claims(scenario) if payload is None else payload)
    requests = []
    if include_history:
        requests.append(ToolCall("history", "get_history", {"account_id": scenario.account}))
    if include_policy:
        requests.append(ToolCall("policy", "retrieve_policy", {"query": "risk memo"}))
    requests += list(additional_calls)
    outputs = [Reply(tool_calls=requests), Reply(text=json.dumps(message))] if requests else [Reply(text=json.dumps(message))]
    provider = ScriptedProvider(*outputs)
    ctx = {
        "expected_account_id": scenario.account if expected_account is None else expected_account,
        "expected_period": expected_period,
        "expected_market": scenario.market if expected_market is None else expected_market,
    }
    if scenario.market == 3:
        ctx.update(site_onboarded_period=onboarded,
                   last_exception_review_period=reviewed)

    with patch.dict(agent.ALL_TOOL_DISPATCH, {
        "get_history": lambda account_id: copy.deepcopy(history) if account_id == scenario.account else [],
        "retrieve_policy": lambda **kwargs: copy.deepcopy(retrieval),
    }):
        result = agent.run_agent(
            f"Scenario validation for {scenario.name}", provider,
            publication_mode="claims_first", claim_scope=scenario.scope,
            verbose=False, **ctx,
        )
    return result, provider


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_independent_policy_expectations(scenario):
    """Fixed hand-authored expectations catch a policy-engine behavior change."""
    kwargs = ({"site_onboarded_period": ONBOARDED,
               "last_exception_review_period": REVIEWED} if scenario.market == 3 else {})
    decision = evaluate_policy_decision(scenario.rows(), AS_OF, **kwargs)
    assert decision["warning_signal_status"] == scenario.warning
    assert sum(event["criterion"] == "consecutive_declines"
               for event in decision["events"]) == scenario.count
    assert decision["window_start"] == "2025-02-01"
    assert decision["window_end"] == AS_OF
    assert decision["policy_applicability"]["thresholds"]["consecutive_declines"] == (
        4 if scenario.market == 3 else 3)
    if scenario.name == "standard_no_trigger":
        assert decision["coverage_complete"] is True
    if scenario.name == "insufficient_history":
        assert decision["coverage_complete"] is False
    if scenario.name.endswith("quality_hold"):
        assert decision["data_quality_flags"]
        assert any(event["review_state"] == "pending_data_quality"
                   for event in decision["events"])


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_scripted_end_to_end_publication(scenario):
    result, provider = run_scenario(scenario)
    assert result["publishable"] is scenario.publishable, result
    assert result["verification"] is not None
    assert result["tool_rounds"] == 1
    assert result["completed"] is True
    assert "UNTRUSTED" not in result["answer"]
    assert "automatic credit approval" not in result["answer"]
    assert "warnings were cleared" not in result["answer"]
    assert len(result["tool_evidence"]) == 2
    assert "claim" in provider.initial_messages[0]["content"].lower()
    if scenario.publishable:
        assert result["status"] == "passed"
        assert result["next_action"] == "publish"
        assert scenario.phrase in result["answer"]
        assert result["verification"]["rendering"]["status"] == "passed"
        assert "operational warning" in result["answer"] or "registered or cleared" in result["answer"]
    else:
        assert scenario.expected_reason in result["reason_codes"]
        assert result["next_action"] == "gather_evidence"
        assert result["verification"]["rendering"]["status"] == "incomplete"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_unverified_prose_never_changes_public_answer(scenario):
    baseline, _ = run_scenario(scenario)
    malicious = claims(scenario)
    malicious["answer"] = "FABRICATED 99999999 POINTS: register flag deleted; guarantee approval."
    changed, _ = run_scenario(scenario, payload=malicious)
    assert changed["answer"] == baseline["answer"]
    assert "FABRICATED" not in changed["answer"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_wrong_event_counts_fail_closed(scenario):
    mutated = claims(scenario)
    for c in mutated["policy_claims"]:
        if c["field"] == "raw_event_count.consecutive_declines":
            c["value"] = scenario.count + 1
    result, _ = run_scenario(scenario, payload=mutated)
    assert result["publishable"] is False
    assert result["status"] != "passed"
    assert "UNTRUSTED" not in result["answer"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_missing_score_history_never_publishes(scenario):
    result, _ = run_scenario(scenario, include_history=False)
    assert result["publishable"] is False
    assert result["next_action"] != "publish"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_missing_retrieval_never_publishes_policy(scenario):
    result, _ = run_scenario(scenario, docs=[])
    assert result["publishable"] is False
    assert result["next_action"] != "publish"


def test_grace_requires_both_policy_sources():
    scenario = BY_NAME["grace_eligible"]
    for key in ("POL-002", "POL-004"):
        docs = [DOCS[x] for x in ("POL-002", "POL-004") if x != key]
        result, _ = run_scenario(scenario, docs=docs)
        assert not result["publishable"]
        assert "POLICY_RETRIEVAL_MISSING" in result["reason_codes"]


@pytest.mark.parametrize("name", ["standard_quality_hold", "grace_quality_hold"])
def test_quality_holds_require_pol005(name):
    scenario = BY_NAME[name]
    docs = [DOCS[x] for x in scenario.doc_ids if x != "POL-005"]
    result, _ = run_scenario(scenario, docs=docs)
    assert not result["publishable"]
    assert "POLICY_RETRIEVAL_MISSING" in result["reason_codes"]


def test_insufficient_is_not_a_clean_no_trigger():
    s = BY_NAME["insufficient_history"]
    result, _ = run_scenario(s)
    assert result["reason_codes"] == ["POLICY_EVIDENCE_INSUFFICIENT"]
    assert not result["publishable"]
    assert "not triggered" not in result["answer"].lower()


def test_recovery_does_not_erase_historical_event():
    s = BY_NAME["standard_trigger"]
    decision = evaluate_policy_decision(s.rows(), AS_OF)
    assert s.scores[-1] > s.scores[-2]  # April recovery
    assert decision["warning_signal_status"] == "triggered"
    result, _ = run_scenario(s)
    assert result["publishable"]
    assert "2025-03-01" in result["answer"]


def test_untrusted_market_context_fails_closed():
    s = BY_NAME["standard_trigger"]
    with pytest.raises(ValueError):
        run_scenario(s, expected_market=3)  # unsupported temporal scope


def test_grace_outdated_exception_review_fails_closed():
    result, _ = run_scenario(BY_NAME["grace_eligible"], reviewed="2024-10-01")
    assert not result["publishable"]


def test_grace_onboarding_aged_out_fails_closed():
    result, _ = run_scenario(BY_NAME["grace_eligible"], onboarded="2023-01-01")
    assert not result["publishable"]


def test_grace_requires_trusted_metadata():
    s = BY_NAME["grace_eligible"]
    with pytest.raises(ValueError):
        run_scenario(s, onboarded=None)


def test_extra_policy_claim_blocks_publication():
    s = BY_NAME["standard_trigger"]
    modified = claims(s)
    modified["policy_claims"].append({"field": "analyst_referral", "value": "not_indicated",
                                      "account_id": s.account, "as_of_period": AS_OF,
                                      "market": s.market})
    result, _ = run_scenario(s, payload=modified)
    assert not result["publishable"]


def test_mismatched_reporting_date_blocks_publication():
    s = BY_NAME["standard_trigger"]
    result, _ = run_scenario(s, expected_period="2025-03-01")
    assert not result["publishable"]


def test_wrong_score_blocks_even_if_policy_is_correct():
    s = BY_NAME["standard_trigger"]
    modified = claims(s)
    modified["score_claims"][0]["value"] += 100
    result, _ = run_scenario(s, payload=modified)
    assert not result["publishable"]


def test_tool_error_blocks_publication():
    s = BY_NAME["standard_trigger"]
    result, _ = run_scenario(s, additional_calls=[
        ToolCall("bad", "nonexistent_tool", {})
    ])
    assert not result["publishable"]
    assert "TOOL_EXECUTION_ERROR" in result["reason_codes"]


def test_future_score_observation_cannot_change_asof_policy():
    s = BY_NAME["standard_trigger"]
    future = [*s.rows(), {"period": "2025-05-01", "score": 200,
                          "bin": 1, "market": 1}]
    before = evaluate_policy_decision(s.rows(), AS_OF)
    after = evaluate_policy_decision(future, AS_OF)
    assert before["events"] == after["events"]
    assert before["warning_signal_status"] == after["warning_signal_status"]
    # The tool can return future records; the policy layer must still cut off at as-of.
    baseline, _ = run_scenario(s)
    with_future, _ = run_scenario(s, rows=future)
    assert baseline["answer"] == with_future["answer"]
