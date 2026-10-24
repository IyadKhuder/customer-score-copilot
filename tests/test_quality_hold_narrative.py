"""Module 5.5.7.2: narrow quality-hold composer and response-gate integration."""
import copy
import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from src import agent
from src.quality_hold_narrative import (
    QualityHoldNarrativeError,
    InsufficientPolicyEvidenceError,
    compose_verified_quality_hold_narrative,
)

ACCOUNT, PERIOD, MARKET = 81000001, "2025-04-01", 1
MONTHS = ["2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01", PERIOD]
SCORES = [700, 530, 520, 510, 500]
ROWS = [dict(period=p, score=s, bin=5, market=MARKET) for p, s in zip(MONTHS, SCORES)]
SHORT_ROWS = ROWS[-2:]
POL002 = {"source": "POL-002_early_warning_thresholds.md", "text": "Three consecutive declines."}
POL005 = {"source": "POL-005_data_quality_and_volatility_flags.md", "text": "Data quality holds."}


def payload(*, insufficient=False):
    return {
        "answer": "UNTRUSTED: All credit restrictions are automatically removed!",
        "score_claims": [
            {"account_id": ACCOUNT, "period": PERIOD, "metric": "score", "value": 500, "market": MARKET},
            {"account_id": ACCOUNT, "period": PERIOD, "metric": "bin", "value": 5, "market": MARKET},
        ],
        "policy_claims": [
            {"field": "warning_signal_status", "value": "insufficient_evidence" if insufficient else "pending_data_quality",
             "account_id": ACCOUNT, "as_of_period": PERIOD, "market": MARKET},
            {"field": "raw_event_count.consecutive_declines", "value": 0 if insufficient else 2,
             "account_id": ACCOUNT, "as_of_period": PERIOD, "market": MARKET},
        ],
        "limitations": [],
    }


def policy_evidence(*, insufficient=False):
    if insufficient:
        events, flags = [], []
    else:
        events = [
            {"criterion": "consecutive_declines", "period": "2025-03-01",
             "evidence_periods": MONTHS[:4], "review_state": "pending_data_quality"},
            {"criterion": "consecutive_declines", "period": PERIOD,
             "evidence_periods": MONTHS[1:], "review_state": "pending_data_quality"},
        ]
        flags = [{"kind": "single_change_over_150", "period": "2025-01-01",
                  "affected_periods": MONTHS[:2], "policy": "POL-005"}]
    return {"window_start": "2025-02-01", "window_end": PERIOD,
            "events": events, "data_quality_flags": flags,
            "policy_sources": ["POL-002", "POL-005"]}


def inner(claim, *, insufficient=False):
    return {"status": "passed", "account_id": ACCOUNT, "as_of_period": PERIOD,
            "market": MARKET, "unchecked_claims": [],
            "checks": [{"field": claim["field"], "claimed": claim["value"],
                        "expected": claim["value"], "context_matches": True,
                        "value_matches": True, "passed": True}],
            "evidence": policy_evidence(insufficient=insufficient)}


def passed_audit(*, insufficient=False):
    data = payload(insufficient=insufficient)
    return {"status": "passed", "schema": {"status": "passed"},
            "answer": data["answer"], "issues": [], "limitations": [],
            "narrative": {"coverage_method": "llm_draft_excluded"},
            "numeric": {"status": "passed", "checks": [
                {"claim": c, "status": "passed", "context_grounded": True, "value_grounded": True}
                for c in data["score_claims"]]},
            "policy": {"status": "passed", "checks": [
                {"claim": c, "status": "passed", "audit": inner(c, insufficient=insufficient)}
                for c in data["policy_claims"]]}}


def tools(*, insufficient=False):
    return [{"tool_name": "get_history", "arguments": {"account_id": ACCOUNT},
             "result": copy.deepcopy(SHORT_ROWS if insufficient else ROWS)}]


def render(audit=None, tool_evidence=None, **kwargs):
    return compose_verified_quality_hold_narrative(
        passed_audit() if audit is None else audit,
        tools() if tool_evidence is None else tool_evidence,
        **(kwargs or dict(account_id=ACCOUNT, as_of_period=PERIOD, market=MARKET)),
    )


def test_pending_dq_renders_verified_event_holds_without_llm_draft():
    output = render()
    assert "verified score was 500 and the risk bin was 5" in output
    assert "is pending data-quality review" in output
    assert "2025-03-01" in output and "2025-04-01" in output
    assert output.count("pending data-quality review under POL-005") == 2
    assert "operational warning was registered or cleared" in output
    assert "UNTRUSTED" not in output
    assert "not triggered" not in output


def test_order_and_untrusted_prose_do_not_matter():
    audit = passed_audit()
    audit["policy"]["checks"].reverse()
    audit["numeric"]["checks"].reverse()
    audit["answer"] = "Unpublished fabricated 1234567 points"
    assert render(audit) == render()


def test_verified_insufficient_evidence_is_never_published():
    with pytest.raises(InsufficientPolicyEvidenceError, match="insufficient historical coverage"):
        render(passed_audit(insufficient=True), tools(insufficient=True))


@pytest.mark.parametrize("edit", [
    lambda a: a.update(status="failed"),
    lambda a: a["schema"].update(status="incomplete"),
    lambda a: a["issues"].append("Unresolved"),
    lambda a: a["limitations"].append("Warning register not available"),
    lambda a: a["narrative"].update(coverage_method="legacy_scan"),
    lambda a: a["numeric"].update(status="failed"),
    lambda a: a["policy"].update(status="incomplete"),
    lambda a: a["numeric"]["checks"].pop(),
    lambda a: a["policy"]["checks"].pop(),
    lambda a: a["policy"]["checks"].append(copy.deepcopy(a["policy"]["checks"][0])),
    lambda a: a["numeric"]["checks"][0].update(context_grounded=False),
    lambda a: a["numeric"]["checks"][0]["claim"].update(market=2),
    lambda a: a["policy"]["checks"][0]["claim"].update(value="not_triggered"),
    lambda a: a["policy"]["checks"][1]["claim"].update(value=0),
    lambda a: a["policy"]["checks"][1]["claim"].update(value=True),
    lambda a: a["policy"]["checks"][1]["claim"].update(field="analyst_referral"),
    lambda a: a["policy"]["checks"][0]["audit"]["checks"][0].update(value_matches=False),
    lambda a: a["policy"]["checks"][0]["audit"].update(as_of_period="2025-03-01"),
    lambda a: a["policy"]["checks"][1]["audit"]["evidence"].update(window_start="2025-01-01"),
    lambda a: a["policy"]["checks"][0]["audit"]["evidence"].update(policy_sources=["POL-002"]),
    lambda a: a["policy"]["checks"][1]["audit"]["evidence"].update(data_quality_flags=[]),
    lambda a: a["policy"]["checks"][1]["audit"]["evidence"]["events"][0].update(review_state="eligible"),
    lambda a: a["policy"]["checks"][0]["audit"]["evidence"]["events"][0].update(period="2025-02-01"),
])
def test_blocks_malformed_or_contradictory_evidence(edit):
    audit = passed_audit()
    edit(audit)
    with pytest.raises(QualityHoldNarrativeError):
        render(audit)


@pytest.mark.parametrize("ctx", [
    {"account_id": ACCOUNT + 1, "as_of_period": PERIOD, "market": MARKET},
    {"account_id": ACCOUNT, "as_of_period": "2025-03-01", "market": MARKET},
    {"account_id": ACCOUNT, "as_of_period": PERIOD, "market": 2},
    {"account_id": ACCOUNT, "as_of_period": PERIOD, "market": True},
])
def test_blocks_mismatched_context(ctx):
    with pytest.raises(QualityHoldNarrativeError):
        render(**ctx)


def test_blocks_missing_or_conflicting_tool_history():
    with pytest.raises(QualityHoldNarrativeError):
        render(tool_evidence=[])
    recorded = tools()
    tampered = copy.deepcopy(recorded[0]); tampered["result"][0]["score"] = 701
    recorded.append(tampered)
    with pytest.raises(QualityHoldNarrativeError):
        render(tool_evidence=recorded)


def test_insufficient_case_rejects_raw_events_even_when_count_claim_is_zero():
    audit = passed_audit(insufficient=True)
    audit["policy"]["checks"][0]["audit"]["evidence"]["events"].append(
        {"criterion": "score_drop", "period": "2025-02-01"})
    with pytest.raises(QualityHoldNarrativeError):
        render(audit, tools(insufficient=True))


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
    def __init__(self, *replies):
        self.replies = list(replies)
        self.messages = None

    def chat(self, messages, tools):
        self.messages = messages
        return self.replies.pop(0)

    def continue_with_tool_results(self, assistant_response, tool_results, tools):
        return self.replies.pop(0)


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

    def check_score_claim(claim, evidence):
        rows = [r for item in evidence if item["tool_name"] == "get_history"
                and item["arguments"]["account_id"] == claim.account_id
                for r in item["result"]]
        ok = any(r["period"] == claim.period and r["market"] == claim.market
                 and r[claim.metric] == claim.value for r in rows)
        return {"value_grounded": ok, "context_grounded": ok}

    guard.check_score_claim = check_score_claim
    guard.extract_score_claims = guard.audit_answer = lambda *_: (_ for _ in ()).throw(
        AssertionError("Legacy prose checker should not run"))

    policy = types.ModuleType("src.policy_claim_guard")

    def fake_policy_audit(claims, history, *, account_id, as_of_period):
        claim = claims[0]
        insufficient = len(history) < 4
        expected = {
            "warning_signal_status": "insufficient_evidence" if insufficient else "pending_data_quality",
            "raw_event_count.consecutive_declines": 0 if insufficient else 2,
        }.get(claim["field"])
        context_ok = (account_id == ACCOUNT and as_of_period == PERIOD
                      and claim["account_id"] == account_id
                      and claim["as_of_period"] == as_of_period and claim["market"] == MARKET)
        history_ok = [r["score"] for r in history] == ([r["score"] for r in SHORT_ROWS]
                    if insufficient else SCORES)
        value_ok = type(claim["value"]) is type(expected) and claim["value"] == expected
        ok = context_ok and history_ok and value_ok and expected is not None
        audit = inner({**claim, "value": expected}, insufficient=insufficient)
        audit["checks"][0].update(claimed=claim["value"], value_matches=value_ok,
                                  context_matches=context_ok, passed=ok)
        audit["status"] = "passed" if ok else "failed"
        return audit

    policy.audit_policy_claims = fake_policy_audit
    with patch.dict(sys.modules, {"src.guardrail": guard, "src.policy_claim_guard": policy}), \
            patch.dict(agent.ALL_TOOL_DISPATCH, {
                "get_history": lambda account_id: copy.deepcopy(ROWS) if account_id == ACCOUNT else [],
                "retrieve_policy": lambda **kwargs: copy.deepcopy([POL002, POL005]),
            }):
        yield


def run_case(data=None, *, insufficient=False, history=True, retrieval=True,
             account=ACCOUNT, period=PERIOD, market=MARKET):
    if insufficient:
        def short_history(account_id):
            return copy.deepcopy(SHORT_ROWS) if account_id == ACCOUNT else []
    else:
        def short_history(account_id):
            return copy.deepcopy(ROWS) if account_id == ACCOUNT else []

    calls = []
    if history:
        calls.append(Call("h", "get_history", {"account_id": ACCOUNT}))
    if retrieval:
        calls.append(Call("p", "retrieve_policy", {"query": "POL-002 POL-005 data quality"}))
    model = Scripted(*([Reply(tool_calls=calls), Reply(text=json.dumps(data or payload(insufficient=insufficient)))]
                        if calls else [Reply(text=json.dumps(data or payload(insufficient=insufficient)))]))
    with patch.dict(agent.ALL_TOOL_DISPATCH, {"get_history": short_history}):
        out = agent.run_agent(
            "Explain verified data quality hold", model,
            publication_mode="claims_first", claim_scope="score_bin_warning_signal_quality_hold",
            expected_account_id=account, expected_period=period,
            expected_market=market, verbose=False,
        )
    return out, model


def test_agent_publishes_verified_hold_through_gate():
    result, model = run_case()
    assert result["publishable"], result
    assert result["verification"]["rendering"]["method"] == "verified_quality_hold_narrative"
    assert "pending data-quality review" in result["answer"]
    assert "UNTRUSTED" not in result["answer"]
    assert "DATA-QUALITY STATUS" in model.messages[0]["content"]


def test_agent_blocks_verified_insufficient_evidence_with_specific_reason():
    result, _ = run_case(insufficient=True)
    assert not result["publishable"], result
    assert result["reason_codes"] == ["POLICY_EVIDENCE_INSUFFICIENT"]
    assert result["next_action"] == "gather_evidence"
    assert "UNTRUSTED" not in result["answer"]


@pytest.mark.parametrize("edit", [
    lambda d: d["score_claims"][0].update(value=999),
    lambda d: d["score_claims"].pop(),
    lambda d: d["policy_claims"].pop(),
    lambda d: d["policy_claims"][0].update(value="not_triggered"),
    lambda d: d["policy_claims"][1].update(value=-1),
    lambda d: d["policy_claims"].append(copy.deepcopy(d["policy_claims"][0])),
    lambda d: d["limitations"].append("missing evidence"),
])
def test_agent_rejects_wrong_or_missing_claims(edit):
    data = payload(); edit(data)
    result, _ = run_case(data)
    assert not result["publishable"]
    assert "UNTRUSTED" not in result["answer"]


@pytest.mark.parametrize("kwargs", [
    {"history": False}, {"retrieval": False},
    {"account": ACCOUNT + 1}, {"period": "2025-03-01"},
])
def test_agent_blocks_missing_inputs(kwargs):
    result, _ = run_case(**kwargs)
    assert not result["publishable"]


def test_agent_requires_both_retrieved_policies():
    with patch.dict(agent.ALL_TOOL_DISPATCH, {"retrieve_policy": lambda **kwargs: [POL002]}):
        result, _ = run_case()
    assert not result["publishable"]
    assert "POLICY_RETRIEVAL_MISSING" in result["reason_codes"]


@pytest.mark.parametrize("market", [2, 3, True, None])
def test_agent_rejects_other_markets(market):
    with pytest.raises(ValueError):
        run_case(market=market)


def test_legacy_mode_rejects_scope():
    with pytest.raises(ValueError):
        agent.run_agent("x", Scripted(Reply(text="{}")),
                        publication_mode="legacy",
                        claim_scope="score_bin_warning_signal_quality_hold",
                        verbose=False)
