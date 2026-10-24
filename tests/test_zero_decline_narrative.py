"""Module 5.5.7.1: verified zero-event composer and agent publication tests."""

import copy
import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from src import agent
from src.zero_decline_narrative import (
    ZeroDeclineNarrativeError, compose_verified_no_decline_narrative,
)

ACCOUNT, PERIOD, MARKET = 81000001, "2025-04-01", 1
PERIODS = ["2024-11-01", "2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01", PERIOD]
VALUES = [600, 606, 604, 608, 609, 610]
ROWS = [dict(period=d, score=s, bin=6, market=MARKET) for d, s in zip(PERIODS, VALUES)]
PASSAGE = [{"source": "POL-002_early_warning_thresholds.md", "text": "Early warning thresholds."}]


def payload():
    return {
        "answer": "UNTRUSTED: all historical warnings have been cleared!",
        "score_claims": [
            {"account_id": ACCOUNT, "period": PERIOD, "metric": "score", "value": 610, "market": MARKET},
            {"account_id": ACCOUNT, "period": PERIOD, "metric": "bin", "value": 6, "market": MARKET},
        ],
        "policy_claims": [
            {"field": "warning_signal_status", "value": "not_triggered",
             "account_id": ACCOUNT, "as_of_period": PERIOD, "market": MARKET},
            {"field": "raw_event_count.consecutive_declines", "value": 0,
             "account_id": ACCOUNT, "as_of_period": PERIOD, "market": MARKET},
        ],
        "limitations": [],
    }


def evidence_record():
    return {"window_start": "2025-02-01", "window_end": PERIOD,
            "events": [], "data_quality_flags": [], "policy_sources": ["POL-002", "POL-005"]}


def inner(claim):
    return {
        "status": "passed", "account_id": ACCOUNT, "as_of_period": PERIOD,
        "market": MARKET, "unchecked_claims": [],
        "checks": [{"field": claim["field"], "claimed": claim["value"],
                    "expected": claim["value"], "context_matches": True,
                    "value_matches": True, "passed": True}],
        "evidence": evidence_record(),
    }


def passed_audit():
    data = payload()
    return {
        "status": "passed", "schema": {"status": "passed"},
        "answer": data["answer"], "issues": [], "limitations": [],
        "narrative": {"coverage_method": "llm_draft_excluded"},
        "numeric": {"status": "passed", "checks": [
            {"claim": c, "status": "passed", "context_grounded": True, "value_grounded": True}
            for c in data["score_claims"]]},
        "policy": {"status": "passed", "checks": [
            {"claim": c, "status": "passed", "audit": inner(c)}
            for c in data["policy_claims"]]},
    }


def tools():
    return [{"tool_name": "get_history", "arguments": {"account_id": ACCOUNT},
             "result": copy.deepcopy(ROWS)}]


def render(audit=None, tool_evidence=None, **kwargs):
    return compose_verified_no_decline_narrative(
        passed_audit() if audit is None else audit,
        tools() if tool_evidence is None else tool_evidence,
        **(kwargs or {"account_id": ACCOUNT, "as_of_period": PERIOD, "market": MARKET}),
    )


def test_no_triggered_and_no_declines_rendered_without_leaking_draft():
    output = render()
    assert "verified score was 610 and the risk bin was 6" in output
    assert "was not triggered" in output
    assert "from 2025-02-01 through 2025-04-01" in output
    assert "no qualifying three-consecutive-month score-decline events" in output
    assert "operational warning was previously registered or cleared" in output
    assert "UNTRUSTED" not in output


def test_order_and_untrusted_answer_independent():
    audit = passed_audit()
    audit["numeric"]["checks"].reverse()
    audit["policy"]["checks"].reverse()
    audit["answer"] = "False claims about 999999 points."
    assert render(audit) == render()


@pytest.mark.parametrize("edit", [
    lambda a: a.update(status="failed"),
    lambda a: a["schema"].update(status="failed"),
    lambda a: a["issues"].append("unresolved"),
    lambda a: a["limitations"].append("missing records"),
    lambda a: a["narrative"].update(coverage_method="legacy_scan"),
    lambda a: a["numeric"].update(status="failed"),
    lambda a: a["policy"].update(status="incomplete"),
    lambda a: a["numeric"]["checks"].pop(),
    lambda a: a["policy"]["checks"].pop(),
    lambda a: a["policy"]["checks"].append(copy.deepcopy(a["policy"]["checks"][0])),
    lambda a: a["numeric"]["checks"][0].update(context_grounded=False),
    lambda a: a["numeric"]["checks"][0]["claim"].update(market=2),
    lambda a: a["policy"]["checks"][0]["claim"].update(value="triggered"),
    lambda a: a["policy"]["checks"][1]["claim"].update(value=1),
    lambda a: a["policy"]["checks"][1]["claim"].update(value=True),
    lambda a: a["policy"]["checks"][1]["claim"].update(field="analyst_referral"),
    lambda a: a["policy"]["checks"][0]["audit"]["checks"][0].update(value_matches=False),
    lambda a: a["policy"]["checks"][0]["audit"]["evidence"].update(window_start="2025-01-01"),
    lambda a: a["policy"]["checks"][1]["audit"]["evidence"].update(window_end="2025-03-01"),
    lambda a: a["policy"]["checks"][1]["audit"]["evidence"].update(events=[
        {"criterion": "score_drop", "period": PERIOD}]),
    lambda a: a["policy"]["checks"][0]["audit"]["evidence"].update(policy_sources=["POL-003"]),
])
def test_rejects_unverified_or_inconsistent_audits(edit):
    audit = passed_audit()
    edit(audit)
    with pytest.raises(ZeroDeclineNarrativeError):
        render(audit)


@pytest.mark.parametrize("context", [
    {"account_id": ACCOUNT + 1, "as_of_period": PERIOD, "market": MARKET},
    {"account_id": ACCOUNT, "as_of_period": "2025-03-01", "market": MARKET},
    {"account_id": ACCOUNT, "as_of_period": PERIOD, "market": 2},
    {"account_id": ACCOUNT, "as_of_period": PERIOD, "market": True},
])
def test_rejects_wrong_context(context):
    with pytest.raises(ZeroDeclineNarrativeError):
        render(**context)


def test_rejects_missing_and_conflicting_history():
    with pytest.raises(ZeroDeclineNarrativeError):
        render(tool_evidence=[])
    records = tools()
    bad = copy.deepcopy(records[0])
    bad["result"][-1]["score"] = 400
    records.append(bad)
    with pytest.raises(ZeroDeclineNarrativeError):
        render(tool_evidence=records)


def test_rejects_observed_event_despite_zero_count():
    records = tools()
    for i, row in enumerate(records[0]["result"]):
        row["score"] = 650 - 6 * i
    with pytest.raises(ZeroDeclineNarrativeError):
        render(tool_evidence=records)


@dataclass
class Call:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class Scripted:
    def __init__(self, *responses):
        self.replies = list(responses)
        self.messages = None

    def chat(self, messages, tools):
        self.messages = messages
        return self.replies.pop(0)

    def continue_with_tool_results(self, assistant_response, tool_results, tools):
        return self.replies.pop(0)


def fake_policy_audit(claims, history, *, account_id, as_of_period):
    claim = claims[0]
    expected = {"warning_signal_status": "not_triggered",
                "raw_event_count.consecutive_declines": 0}.get(claim["field"])
    context_ok = (account_id == ACCOUNT and as_of_period == PERIOD
                  and claim["account_id"] == account_id
                  and claim["as_of_period"] == as_of_period and claim["market"] == MARKET)
    history_ok = [r["score"] for r in history] == VALUES
    value_ok = type(claim["value"]) is type(expected) and claim["value"] == expected
    ok = context_ok and history_ok and value_ok and expected is not None
    audit = inner({**claim, "value": expected})
    audit["checks"][0].update(claimed=claim["value"], value_matches=value_ok,
                              context_matches=context_ok, passed=ok)
    audit["status"] = "passed" if ok else "failed"
    return audit


@pytest.fixture(autouse=True)
def fake_policy_and_guard():
    guard = types.ModuleType("src.guardrail")

    @dataclass(frozen=True)
    class ScoreClaim:
        account_id: int
        period: str
        metric: str
        value: int
        market: int

    guard.ScoreClaim = ScoreClaim

    def check_score_claim(c, evidence):
        rows = [r for t in evidence if t.get("tool_name") == "get_history"
                and t["arguments"].get("account_id") == c.account_id
                for r in t["result"]]
        ok = any(r["period"] == c.period and r["market"] == c.market
                 and r[c.metric] == c.value for r in rows)
        return {"value_grounded": ok, "context_grounded": ok}

    guard.check_score_claim = check_score_claim
    guard.audit_answer = guard.extract_score_claims = lambda *_: (_ for _ in ()).throw(
        AssertionError("legacy narrative checker called"))
    policy = types.ModuleType("src.policy_claim_guard")
    policy.audit_policy_claims = fake_policy_audit
    with patch.dict(sys.modules, {"src.guardrail": guard, "src.policy_claim_guard": policy}), \
            patch.dict(agent.ALL_TOOL_DISPATCH, {
                "get_history": lambda account_id: copy.deepcopy(ROWS) if account_id == ACCOUNT else [],
                "retrieve_policy": lambda **kwargs: copy.deepcopy(PASSAGE),
            }):
        yield


def run_case(data=None, *, history=True, retrieval=True, account=ACCOUNT,
             period=PERIOD, market=MARKET, scope="score_bin_warning_signal_no_declines"):
    calls = []
    if history:
        calls.append(Call("h", "get_history", {"account_id": ACCOUNT}))
    if retrieval:
        calls.append(Call("p", "retrieve_policy", {"query": "POL-002 decline thresholds"}))
    replies = ([Reply(tool_calls=calls), Reply(text=json.dumps(payload() if data is None else data))]
               if calls else [Reply(text=json.dumps(payload() if data is None else data))])
    model = Scripted(*replies)
    output = agent.run_agent("Evaluate verified absence of decline events", model,
        publication_mode="claims_first", claim_scope=scope,
        expected_account_id=account, expected_period=period, expected_market=market,
        verbose=False)
    return output, model


def test_agent_publishes_no_decline_report_via_gate():
    result, model = run_case()
    assert result["publishable"], result
    assert result["status"] == "passed" and result["next_action"] == "publish"
    assert result["verification"]["rendering"]["method"] == "verified_no_decline_narrative"
    assert "no qualifying three-consecutive-month" in result["answer"]
    assert "UNTRUSTED" not in result["answer"]
    prompt = model.messages[0]["content"]
    assert "verified NO-DECLINE report" in prompt
    assert f"account_id={ACCOUNT}, as_of_period={PERIOD}, market={MARKET}" in prompt


def test_agent_never_publishes_model_draft():
    data = payload()
    data["answer"] = "All restrictions lifted; score falls 9999!"
    result, _ = run_case(data)
    assert result["publishable"]
    assert "9999" not in result["answer"]


@pytest.mark.parametrize("edit", [
    lambda d: d["score_claims"].pop(),
    lambda d: d["policy_claims"].pop(),
    lambda d: d["policy_claims"][0].update(value="triggered"),
    lambda d: d["policy_claims"][1].update(value=1),
    lambda d: d["policy_claims"][1].update(field="analyst_referral"),
    lambda d: d["score_claims"][0].update(value=999),
    lambda d: d["limitations"].append("Unresolved limitation"),
])
def test_agent_rejects_inaccurate_or_incomplete_claims(edit):
    data = payload()
    edit(data)
    result, _ = run_case(data)
    assert not result["publishable"]
    assert "UNTRUSTED" not in result["answer"]


@pytest.mark.parametrize("kwargs", [
    {"history": False},
    {"retrieval": False},
    {"account": ACCOUNT + 1},
    {"period": "2025-03-01"},
])
def test_agent_rejects_missing_evidence_or_wrong_context(kwargs):
    result, _ = run_case(**kwargs)
    assert not result["publishable"]


def test_agent_blocks_unrelated_policy_retrieval():
    with patch.dict(agent.ALL_TOOL_DISPATCH, {"retrieve_policy": lambda **kwargs: [
        {"source": "POL-003_actions.md", "text": "Unrelated"}]}):
        output, _ = run_case()
    assert not output["publishable"]
    assert "POLICY_RETRIEVAL_MISSING" in output["reason_codes"]


@pytest.mark.parametrize("market", [2, 3, None, True])
def test_agent_rejects_nonstandard_or_missing_market(market):
    with pytest.raises(ValueError):
        run_case(market=market)


def test_existing_full_scope_still_rejects_zero_case():
    out, _ = run_case(scope="score_bin_warning_signal_decline_events")
    assert not out["publishable"]


def test_legacy_mode_rejects_new_scope():
    with pytest.raises(ValueError):
        agent.run_agent("x", Scripted(Reply(text="{}")),
            publication_mode="legacy", claim_scope="score_bin_warning_signal_no_declines", verbose=False)
