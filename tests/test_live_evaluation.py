"""Module 6.4 offline tests: NO network or paid model requests.

A fake provider is injected into a fake agent to exercise the isolated tool
boundary, CLI authorization, serialization, and module 6.2/6.3 integration.
The developer will run the real adapter against live providers separately.
"""
from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

import src
from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import FixtureValidationError, load_fixtures, save_report
from src.evaluation_metrics import score_report, validate_run_report
from src.live_evaluation import (
    _check_client, _check_pair, run_live_benchmark, run_live_case,
)
from scripts.run_live_benchmark_64 import main


class FakeClient:
    def chat(self, *, messages, tools):
        return None

    def continue_with_tool_results(self, *, assistant_response, tool_results, tools):
        return None


def contract():
    b = load_benchmark()
    f = load_fixtures(benchmark=b)
    return b, f


def pair(index=0):
    b, f = contract()
    case = b["cases"][index]
    fixture = next(x for x in f["fixtures"] if x["scenario_id"] == case["scenario_id"])
    return copy.deepcopy(case), copy.deepcopy(fixture)


def mock_result(*, policy_ids, publishable=True):
    return {
        "answer": "Safe public narration" if publishable else "BLOCKED private message",
        "completed": True,
        "status": "passed" if publishable else "incomplete",
        "publishable": publishable,
        "next_action": "publish" if publishable else "gather_evidence",
        "reason_codes": [] if publishable else ["POLICY_EVIDENCE_INSUFFICIENT"],
        "tool_rounds": 1,
        "tool_evidence": [
            {"tool_name": "get_history", "result": [{"score": 700}]},
            {"tool_name": "retrieve_policy", "result": [
                {"source": x + "_synthetic_policy.md"} for x in policy_ids
            ]},
        ],
        "verification": {"answer": "PRIVATE LLM DRAFT with provider secrets"},
    }


class FakeAgent:
    def __init__(self, behavior=None):
        self.ALL_TOOLS = [
            {"name": "get_history", "input_schema": {}},
            {"name": "retrieve_policy", "input_schema": {}},
            {"name": "get_latest_score", "input_schema": {}},
        ]
        self.ALL_TOOL_DISPATCH = {"get_latest_score": self.forbidden}
        self.seen = []
        self.behavior = behavior

    @staticmethod
    def forbidden(**kwargs):
        raise AssertionError("Production tool was called")

    def run_agent(self, question, llm, **kwargs):
        assert set(self.ALL_TOOL_DISPATCH) == {"get_history", "retrieve_policy"}
        assert {x["name"] for x in sys.modules["src.agent"].ALL_TOOLS} == {"get_history", "retrieve_policy"}
        assert kwargs["verbose"] is False
        assert isinstance(question, str)
        assert isinstance(llm, FakeClient)
        history_fn = self.ALL_TOOL_DISPATCH["get_history"]
        policy_fn = self.ALL_TOOL_DISPATCH["retrieve_policy"]
        rows = history_fn(account_id=kwargs["expected_account_id"])
        docs = policy_fn(query="risk triggers", k=3)
        self.seen.append((question, kwargs, rows, docs))
        if self.behavior is not None:
            return self.behavior(self, kwargs, rows, docs)
        return mock_result(policy_ids=[x["source"].split("_")[0] for x in docs])


@pytest.fixture
def fake_agent():
    agent = FakeAgent()
    module = types.ModuleType("src.agent")
    module.ALL_TOOLS = agent.ALL_TOOLS
    module.ALL_TOOL_DISPATCH = agent.ALL_TOOL_DISPATCH
    module.run_agent = agent.run_agent
    # `from src import agent` can return a cached module attribute, too.
    with patch.dict(sys.modules, {"src.agent": module}), patch.object(src, "agent", module, create=True):
        yield agent
    assert module.ALL_TOOL_DISPATCH == {"get_latest_score": agent.forbidden}
    assert len(module.ALL_TOOLS) == 3


def test_supported_client(fake_agent):
    case, fixture = pair()
    response = run_live_case(case, fixture, FakeClient())
    assert response["publishable"] is True
    assert len(fake_agent.seen) == 1


def test_live_adapter_receives_real_question_but_not_expected_labels(fake_agent):
    case, fixture = pair()
    run_live_case(case, fixture, FakeClient())
    q, kwargs, rows, docs = fake_agent.seen[0]
    assert q == case["question"]
    assert "expected" not in kwargs and "warning_signal_status" not in kwargs
    assert rows[-1]["score"] == fixture["scores"][-1]
    assert all(row["market"] == case["request"]["market"] for row in rows)
    assert docs[0]["source"].startswith("POL-")


@pytest.mark.parametrize("case_idx", range(6))
def test_all_six_claim_scopes_preserved(fake_agent, case_idx):
    case, fixture = pair(case_idx)
    run_live_case(case, fixture, FakeClient())
    _, kwargs, rows, docs = fake_agent.seen[0]
    assert kwargs["claim_scope"] == case["request"]["claim_scope"]
    assert kwargs["expected_period"] == case["request"]["as_of_period"]
    assert kwargs["expected_market"] == case["request"]["market"]
    assert kwargs["expected_account_id"] == case["request"]["account_id"]
    assert len(rows) == len(fixture["periods"])
    assert len(docs) == len(fixture["policy_ids"])
    if case["request"]["claim_scope"] == "grace_decline_events":
        assert kwargs["site_onboarded_period"] == case["request"]["site_onboarded_period"]
        assert kwargs["last_exception_review_period"] == case["request"]["last_exception_review_period"]
    else:
        assert "site_onboarded_period" not in kwargs


def test_cross_account_history_is_empty(fake_agent):
    def behavior(a, ctx, rows, docs):
        assert a.ALL_TOOL_DISPATCH["get_history"](account_id=999999) == []
        assert a.ALL_TOOL_DISPATCH["get_history"](account_id=True) == []
        return mock_result(policy_ids=[])
    fake_agent.behavior = behavior
    run_live_case(*pair(), FakeClient())


def test_fixture_rows_are_defensive_copies(fake_agent):
    def behavior(a, ctx, rows, docs):
        other = a.ALL_TOOL_DISPATCH["get_history"](account_id=ctx["expected_account_id"])
        other[0]["score"] = -999
        new_rows = a.ALL_TOOL_DISPATCH["get_history"](account_id=ctx["expected_account_id"])
        assert new_rows[0]["score"] != -999
        return mock_result(policy_ids=[])
    fake_agent.behavior = behavior
    case, fix = pair()
    first_score = fix["scores"][0]
    run_live_case(case, fix, FakeClient())
    assert fix["scores"][0] == first_score


@pytest.mark.parametrize("bad_value", [None, "", " ", 5, False])
def test_reject_bad_policy_queries(fake_agent, bad_value):
    def behavior(a, ctx, rows, docs):
        with pytest.raises(ValueError):
            a.ALL_TOOL_DISPATCH["retrieve_policy"](query=bad_value)
        return mock_result(policy_ids=[])
    fake_agent.behavior = behavior
    run_live_case(*pair(), FakeClient())


@pytest.mark.parametrize("bad_k", [0, -1, 11, True, None, "3"])
def test_reject_bad_policy_k(fake_agent, bad_k):
    def behavior(a, ctx, rows, docs):
        with pytest.raises(ValueError):
            a.ALL_TOOL_DISPATCH["retrieve_policy"](query="trigger", k=bad_k)
        return mock_result(policy_ids=[])
    fake_agent.behavior = behavior
    run_live_case(*pair(), FakeClient())


def test_policy_k_and_defensive_copies(fake_agent):
    case, fix = pair(2)
    def behavior(a, ctx, rows, docs):
        small = a.ALL_TOOL_DISPATCH["retrieve_policy"](query="risk", k=1)
        assert len(small) == 1
        small[0]["text"] = "TAMPERED"
        fresh = a.ALL_TOOL_DISPATCH["retrieve_policy"](query="risk")
        assert fresh[0]["text"] != "TAMPERED"
        return mock_result(policy_ids=[])
    fake_agent.behavior = behavior
    run_live_case(case, fix, FakeClient())


@pytest.mark.parametrize("attr", ["chat", "continue_with_tool_results"])
def test_reject_incomplete_adapter(attr, fake_agent):
    class Incomplete: pass
    obj = Incomplete()
    setattr(obj, attr, lambda **kwargs: None)
    with pytest.raises(TypeError, match="Provider adapter"):
        run_live_case(*pair(), obj)


@pytest.mark.parametrize("rounds", [0, 9, True, None, "4", 1.5])
def test_reject_invalid_round_limit(rounds, fake_agent):
    with pytest.raises(ValueError, match="max_tool_rounds"):
        run_live_case(*pair(), FakeClient(), max_tool_rounds=rounds)


@pytest.mark.parametrize("mutate", [
    lambda c,f: f.update(scenario_id="wrong"),
    lambda c,f: f.update(account_id=123456),
    lambda c,f: f.update(market=5),
    lambda c,f: f.update(periods=["2025-03-01"]),
    lambda c,f: f.update(policy_ids=["POL-999"]),
    lambda c,f: f.update(scores=[1]),
    lambda c,f: c["request"].update(publication_mode="legacy"),
])
def test_bad_pair_is_rejected_before_agent_import(fake_agent, mutate):
    case, fix = pair()
    mutate(case, fix)
    with pytest.raises(FixtureValidationError):
        run_live_case(case, fix, FakeClient())
    assert fake_agent.seen == []


def test_tool_registry_restored_on_provider_failure(fake_agent):
    fake_agent.behavior = lambda *_: (_ for _ in ()).throw(RuntimeError("SECRET API ERROR"))
    with pytest.raises(RuntimeError):
        run_live_case(*pair(), FakeClient())
    assert fake_agent.seen


def test_tool_error_sanitized_in_runner(fake_agent):
    fake_agent.behavior = lambda *_: (_ for _ in ()).throw(RuntimeError("SECRET KEY 123"))
    b, f = contract()
    report = run_live_benchmark(FakeClient(), provider_id="gemini.test", benchmark=b, fixtures=f)
    assert report["execution_mode"] == "provider_adapter"
    assert all(x["error_type"] == "RuntimeError" for x in report["cases"])
    assert all(x["observed"] is None for x in report["cases"])
    assert "SECRET KEY" not in json.dumps(report)


def test_runner_does_not_persist_unverified_draft(fake_agent):
    b, f = contract()
    report = run_live_benchmark(FakeClient(), provider_id="gemini.test", benchmark=b, fixtures=f)
    assert len(report["cases"]) == 6
    assert all(row["error_type"] is None for row in report["cases"])
    assert all(row["observed"]["publishable"] for row in report["cases"])
    raw = json.dumps(report)
    assert "PRIVATE LLM DRAFT" not in raw
    assert "provider secrets" not in raw
    assert "scores\"" not in raw
    validate_run_report(report, b, f)


def test_blocked_answer_not_persisted(fake_agent):
    fake_agent.behavior = lambda a,ctx,rows,docs: mock_result(
        policy_ids=["POL-002"], publishable=False,
    )
    b, f = contract()
    report = run_live_benchmark(FakeClient(), provider_id="anthropic.test", benchmark=b, fixtures=f)
    assert all(row["observed"]["public_answer"] == "" for row in report["cases"])
    assert "BLOCKED private message" not in json.dumps(report)


def test_runner_scoring_integration(fake_agent):
    b, f = contract()
    report = run_live_benchmark(FakeClient(), provider_id="gemini.mock", benchmark=b, fixtures=f)
    scores = score_report(report, benchmark=b, fixtures=f)
    assert scores["provider_id"] == "gemini.mock"
    assert scores["metrics"]["cases_total"] == 6
    # The fake does NOT know the right narrative or negative-case response.
    assert scores["metrics"]["cases_passed_all_checks"] < 6


@pytest.mark.parametrize("bad_label", ["", "bad label", "/tmp/key", "a"*65, None, True])
def test_runner_rejects_bad_provider_id(fake_agent, bad_label):
    with pytest.raises(ValueError):
        run_live_benchmark(FakeClient(), provider_id=bad_label)


def test_cli_requires_opt_in_and_never_calls_client_factory(tmp_path):
    calls = []
    def factory(p):
        calls.append(p)
        return FakeClient()
    with pytest.raises(SystemExit):
        main(["--provider", "gemini", "--model-label", "model", "--output", str(tmp_path / "a.json")],
             client_factory=factory)
    assert calls == []


@pytest.mark.parametrize("bad_label", ["abc xyz", "../thing", "a" * 51, "__?"])
def test_cli_rejects_bad_model_label_without_network(tmp_path, bad_label):
    with pytest.raises(SystemExit):
        main(["--provider", "gemini", "--model-label", bad_label,
              "--output", str(tmp_path / "a.json"), "--confirm-live-api"],
             client_factory=lambda _: (_ for _ in ()).throw(AssertionError("NETWORK")))


def test_cli_refuses_existing_report_without_client_factory(tmp_path):
    path = tmp_path / "exists.json"
    path.write_text("old", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--provider", "gemini", "--model-label", "model",
              "--output", str(path), "--confirm-live-api"],
             client_factory=lambda _: (_ for _ in ()).throw(AssertionError("NETWORK")))
    assert path.read_text(encoding="utf-8") == "old"


def test_cli_writes_scannable_report_without_network(tmp_path, fake_agent):
    path = tmp_path / "report.json"
    exit_code = main(["--provider", "gemini", "--model-label", "model-v1",
                      "--output", str(path), "--confirm-live-api"],
                     client_factory=lambda _: FakeClient())
    assert exit_code == 0
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["provider_id"] == "gemini.model-v1"
    assert doc["execution_mode"] == "provider_adapter"
    assert len(doc["cases"]) == 6


def test_cli_reports_errors_with_nonzero_exit(tmp_path, fake_agent):
    fake_agent.behavior = lambda *_: (_ for _ in ()).throw(ValueError("PRIVATE"))
    path = tmp_path / "broken.json"
    rc = main(["--provider", "anthropic", "--model-label", "model-v2",
               "--output", str(path), "--confirm-live-api"],
              client_factory=lambda _: FakeClient())
    assert rc == 1
    assert "PRIVATE" not in path.read_text(encoding="utf-8")


def test_cli_rejects_invalid_max_rounds(tmp_path):
    with pytest.raises(SystemExit):
        main(["--provider", "gemini", "--model-label", "model",
              "--output", str(tmp_path / "x.json"),
              "--max-tool-rounds", "9", "--confirm-live-api"],
             client_factory=lambda _: (_ for _ in ()).throw(AssertionError("NETWORK")))


def test_save_report_no_overwrite(tmp_path, fake_agent):
    report = run_live_benchmark(FakeClient(), provider_id="test")
    path = tmp_path / "saved.json"
    save_report(report, path)
    with pytest.raises(FileExistsError):
        save_report(report, path)


def test_negative_case_is_not_automatically_marked_passed(fake_agent):
    b, f = contract()
    report = run_live_benchmark(FakeClient(), provider_id="test", benchmark=b, fixtures=f)
    scores = score_report(report, benchmark=b, fixtures=f)
    negative = next(x for x in scores["cases"] if x["case_id"] == "CF-004")
    assert negative["test_type"] == "negative"
    assert not negative["passed"]
