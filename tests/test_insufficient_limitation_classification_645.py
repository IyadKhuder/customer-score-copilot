"""Module 6.4.5.1: classify verified evidence gaps without publishing drafts.

Negative-test expectation: a verified `insufficient_evidence` result must
produce a dedicated gather-evidence BLOCK, even when the model truthfully
includes missing-history limitations. Other limitations still fail closed.
"""
from __future__ import annotations

import copy
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
ROWS = [
    {"period": "2025-03-01", "score": 510, "bin": 5, "market": MARKET},
    {"period": PERIOD, "score": 500, "bin": 5, "market": MARKET},
]
POL002 = {"source": "POL-002_early_warning_thresholds.md", "text": "Rolling signal rules."}
POL005 = {"source": "POL-005_data_quality_and_volatility_flags.md", "text": "Data quality review."}


def audit_fixture(*, status="insufficient_evidence", count=0, limitations=None):
    limitations = ["Earlier score history is unavailable"] if limitations is None else limitations
    scores = [
        {"account_id": ACCOUNT, "period": PERIOD, "metric": metric,
         "value": value, "market": MARKET}
        for metric, value in (("score", 500), ("bin", 5))
    ]
    policy_claims = [
        {"field": name, "value": value, "account_id": ACCOUNT,
         "as_of_period": PERIOD, "market": MARKET}
        for name, value in (
            ("warning_signal_status", status),
            ("raw_event_count.consecutive_declines", count),
        )
    ]
    source_evidence = {
        "window_start": "2025-02-01", "window_end": PERIOD,
        "events": [], "data_quality_flags": [],
        "policy_sources": ["POL-002", "POL-005"],
    }

    def policy_check(claim):
        return {
            "claim": claim, "status": "passed",
            "audit": {
                "status": "passed", "account_id": ACCOUNT,
                "as_of_period": PERIOD, "market": MARKET,
                "unchecked_claims": [],
                "checks": [{
                    "field": claim["field"], "claimed": claim["value"],
                    "expected": claim["value"], "context_matches": True,
                    "value_matches": True, "passed": True,
                }],
                "evidence": copy.deepcopy(source_evidence),
            },
        }

    return {
        "status": "passed", "schema": {"status": "passed"},
        "answer": "UNTRUSTED DRAFT: pretend the account is cleared",
        "limitations": list(limitations), "issues": [],
        "narrative": {"coverage_method": "llm_draft_excluded"},
        "numeric": {"status": "passed", "checks": [
            {"claim": item, "status": "passed",
             "context_grounded": True, "value_grounded": True} for item in scores
        ]},
        "policy": {"status": "passed", "checks": [policy_check(x) for x in policy_claims]},
    }


def history_evidence():
    return [{"tool_name": "get_history", "arguments": {"account_id": ACCOUNT},
             "result": copy.deepcopy(ROWS)}]


def compose(audit):
    return compose_verified_quality_hold_narrative(
        audit, history_evidence(), account_id=ACCOUNT,
        as_of_period=PERIOD, market=MARKET,
    )


def test_verified_insufficient_gap_with_limitations_has_dedicated_block():
    with pytest.raises(InsufficientPolicyEvidenceError, match="insufficient historical coverage"):
        compose(audit_fixture())


def test_verified_insufficient_without_limitations_remains_dedicated_block():
    with pytest.raises(InsufficientPolicyEvidenceError):
        compose(audit_fixture(limitations=[]))


@pytest.mark.parametrize("limits", [
    ["Missing January observations"],
    ["Incomplete lookback", "No earlier baseline"],
])
def test_variations_of_insufficient_limitations_still_block(limits):
    with pytest.raises(InsufficientPolicyEvidenceError):
        compose(audit_fixture(limitations=limits))


@pytest.mark.parametrize("alter", [
    lambda a: a["issues"].append("Unresolved validation issue"),
    lambda a: a.update(status="incomplete"),
    lambda a: a["schema"].update(status="failed"),
    lambda a: a["numeric"]["checks"][0].update(context_grounded=False),
    lambda a: a["numeric"]["checks"][0]["claim"].update(account_id=ACCOUNT+1),
    lambda a: a["numeric"]["checks"][1].update(value_grounded=False),
    lambda a: a["policy"]["checks"][0]["audit"]["checks"][0].update(value_matches=False),
    lambda a: a["policy"]["checks"][0]["claim"].update(as_of_period="2025-03-01"),
    lambda a: a["policy"]["checks"][1]["audit"]["evidence"]["events"].append(
        {"criterion": "consecutive_declines", "period": PERIOD}),
    lambda a: a["policy"]["checks"].append(copy.deepcopy(a["policy"]["checks"][0])),
])
def test_unverified_or_contradictory_insufficiency_is_not_reclassified(alter):
    audit = audit_fixture()
    alter(audit)
    with pytest.raises(QualityHoldNarrativeError) as ex:
        compose(audit)
    assert not isinstance(ex.value, InsufficientPolicyEvidenceError)


def test_pending_status_with_limitations_keeps_publication_blocked():
    # This test checks the LIMITATIONS gate before the more detailed POL-005
    # event-matching checks. No 'pending' narrative may discard those gaps.
    audit = audit_fixture(status="pending_data_quality", count=1)
    with pytest.raises(QualityHoldNarrativeError, match="Unresolved limitations") as ex:
        compose(audit)
    assert not isinstance(ex.value, InsufficientPolicyEvidenceError)


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
    def chat(self, messages, tools):
        return Reply(tool_calls=[
            Call("h", "get_history", {"account_id": ACCOUNT}),
            Call("p", "retrieve_policy", {"query": "POL-002 POL-005"}),
        ])

    def continue_with_tool_results(self, assistant_response, tool_results, tools):
        return Reply(text="unpublished provider content")


def agent_run(audit):
    dispatch = {
        "get_history": lambda account_id: copy.deepcopy(ROWS) if account_id == ACCOUNT else [],
        "retrieve_policy": lambda query: copy.deepcopy([POL002, POL005]),
    }
    with patch.dict(agent.ALL_TOOL_DISPATCH, dispatch), \
            patch.object(agent, "verify_structured_response", return_value=copy.deepcopy(audit)):
        return agent.run_agent(
            "Assess this account's policy warning coverage", Scripted(),
            publication_mode="claims_first",
            claim_scope="score_bin_warning_signal_quality_hold",
            expected_account_id=ACCOUNT, expected_period=PERIOD,
            expected_market=MARKET, verbose=False,
        )


def test_agent_returns_specific_gap_reason_and_never_publishes_draft():
    out = agent_run(audit_fixture())
    assert out["publishable"] is False
    assert out["reason_codes"] == ["POLICY_EVIDENCE_INSUFFICIENT"]
    assert out["next_action"] == "gather_evidence"
    assert out["verification"]["rendering"]["status"] == "incomplete"
    assert "UNTRUSTED" not in out["answer"]
    assert "unpublished" not in out["answer"].lower()


def test_agent_pending_with_limitations_still_fails_closed():
    out = agent_run(audit_fixture(status="pending_data_quality", count=1))
    assert out["publishable"] is False
    assert out["reason_codes"] == ["COMPOSITION_UNSUPPORTED"]
    assert out["next_action"] == "revise_response"
    assert "UNTRUSTED" not in out["answer"]
