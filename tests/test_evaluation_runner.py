"""Module 6.2: benchmark runner, fixture isolation, and safe result recording.

Offline only: uses stand-in executor where appropriate, and a scripted-agent
smoke test with patched dispatch. No live LLM, database, or API keys are used.
"""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import (
    FixtureValidationError, _normal_observation, _policy_ids_from_tool_evidence,
    load_fixtures, run_benchmark, run_scripted_case, save_report,
    validate_fixtures,
)


def manifest():
    return copy.deepcopy(load_benchmark())


def fixtures():
    return copy.deepcopy(load_fixtures(benchmark=manifest()))


def success_result(*, publishable=True, answer="Verified public answer"):
    return {
        "answer": answer,
        "completed": True,
        "status": "passed" if publishable else "incomplete",
        "publishable": publishable,
        "next_action": "publish" if publishable else "gather_evidence",
        "reason_codes": [] if publishable else ["POLICY_EVIDENCE_INSUFFICIENT"],
        "tool_rounds": 1,
        "tool_evidence": [
            {"tool_name": "get_history", "result": [{"score": 650}]},
            {"tool_name": "retrieve_policy", "result": [
                {"source": "POL-002_early_warning_thresholds.md", "text": "relevant"},
                {"source": "POL-005_data_quality_and_volatility_flags.md", "text": "hold"},
            ]},
        ],
        "verification": {"answer": "SECRET UNVERIFIED DRAFT"},
    }


def test_fixture_manifest_has_six_named_cases():
    data = fixtures()
    assert len(data["fixtures"]) == 6
    assert {f["scenario_id"] for f in data["fixtures"]} == {
        "standard_trigger", "standard_no_trigger", "standard_quality_hold",
        "insufficient_history", "grace_eligible", "grace_quality_hold",
    }


def test_fixture_rows_are_pinned_to_new_synthetic_scenarios():
    by_id = {f["scenario_id"]: f for f in fixtures()["fixtures"]}
    assert by_id['standard_trigger']["scores"] == [286, 286, 286, 286, 286, 286, 282, 275, 267, 271]
    assert by_id['standard_no_trigger']["scores"] == [218, 221, 224, 227, 230, 233, 236, 239, 242, 245]
    assert by_id['standard_quality_hold']["scores"] == [392, 392, 392, 392, 392, 392, 216, 207, 198, 189]
    assert by_id['insufficient_history']["scores"] == [174, 177, 181]
    assert by_id['grace_eligible']["scores"] == [278, 278, 278, 278, 272, 265, 257, 248, 251, 254]
    assert by_id['grace_quality_hold']["scores"] == [278, 278, 278, 278, 272, 265, 257, 248, 257, 261]


def test_all_cases_have_identical_request_and_fixture_context():
    b, f = manifest(), fixtures()
    by_id = {x["scenario_id"]: x for x in f["fixtures"]}
    for case in b["cases"]:
        found = by_id[case["scenario_id"]]
        assert found["account_id"] == case["request"]["account_id"]
        assert found["market"] == case["request"]["market"]
        assert found["periods"][-1] == case["request"]["as_of_period"]
        assert set(found["policy_ids"]) == set(case["expected"]["required_policy_ids"])


@pytest.mark.parametrize("field,bad,match", [
    ("account_id", 77, "account/market"),
    ("account_id", True, "account/market"),
    ("market", 3, "account/market"),
    ("bins", [True]*10, "risk bin"),
    ("bins", [11]*10, "risk bin"),
    ("scores", [286], "periods/scores"),
    ("scores", [True, 282, 275, 267, 271], "scores"),
    ("scores", [286, 282, 275, 267, 1001], "scores"),
    ("policy_ids", ["POL-002", "POL-999"], "policy memo"),
    ("policy_ids", ["POL-002", "POL-002"], "policy memo"),
    ("policy_ids", ["POL-005"], "disagree"),
    ("periods", ["1991-03-01", "1991-05-01"] + [f"1991-{m:02d}-01" for m in range(5,13)], "chronological"),
    ("periods", ["1991-03-02"] + [f"1991-{m:02d}-01" for m in range(4,13)], "month starts"),
    ("periods", ["1991-03-01", "1991-05-01"] + [f"1991-{m:02d}-01" for m in range(5,13)], "chronological"),
])
def test_reject_invalid_fixture_field(field, bad, match):
    data = fixtures()
    data["fixtures"][0][field] = bad
    with pytest.raises(FixtureValidationError, match=match):
        validate_fixtures(data, manifest())


def test_reject_fixture_that_ends_early():
    data = fixtures()
    f = data["fixtures"][0]
    f["periods"].pop()
    f["scores"].pop()
    f["bins"].pop()
    with pytest.raises(FixtureValidationError, match="as-of"):
        validate_fixtures(data, manifest())


def test_reject_duplicate_fixture():
    data = fixtures()
    data["fixtures"][1] = copy.deepcopy(data["fixtures"][0])
    with pytest.raises(FixtureValidationError, match="duplicated"):
        validate_fixtures(data, manifest())


def test_reject_extra_fixture():
    data = fixtures()
    data["fixtures"].append(copy.deepcopy(data["fixtures"][0]))
    with pytest.raises(FixtureValidationError, match="count"):
        validate_fixtures(data, manifest())


def test_reject_wrong_fixture_version():
    data = fixtures()
    data["schema_version"] = 99
    with pytest.raises(FixtureValidationError, match="version"):
        validate_fixtures(data, manifest())


def test_load_from_custom_fixture_path(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixtures()), encoding="utf-8")
    assert load_fixtures(path, benchmark=manifest()) == fixtures()


def test_malformed_fixture_file(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text('{"oops":', encoding="utf-8")
    with pytest.raises(FixtureValidationError, match="Malformed"):
        load_fixtures(path)


def test_runner_calls_executor_once_per_case_in_fixed_order():
    seen = []

    def execute(case, fixture):
        seen.append((case["id"], fixture["scenario_id"]))
        return success_result()

    result = run_benchmark(execute)
    assert len(result["cases"]) == 6
    assert [r["case_id"] for r in result["cases"]] == [f"CF-{i:03}" for i in range(1, 7)]
    assert [v[1] for v in seen] == [c["scenario_id"] for c in manifest()["cases"]]
    assert result["benchmark_id"] == "scorecopilot_synthetic_claim_first_v2"
    assert result["provider_id"] == "scripted_offline"
    assert result["execution_mode"] == "scripted_offline"


def test_runner_passes_defensive_copies():
    source = fixtures()
    original = copy.deepcopy(source)

    def execute(case, fixture):
        case["expected"].clear()
        fixture["scores"].clear()
        return success_result()

    run_benchmark(execute, fixtures=source)
    assert source == original


def test_runner_records_observation_and_not_expected_labels_as_scores():
    report = run_benchmark(lambda c, f: success_result())
    rec = report["cases"][0]
    assert rec["error_type"] is None
    assert rec["observed"]["publishable"] is True
    assert rec["observed"]["tool_names"] == ["get_history", "retrieve_policy"]
    assert rec["observed"]["retrieved_policy_ids"] == ["POL-002", "POL-005"]
    assert rec["observed"]["public_answer"] == "Verified public answer"
    assert "expected" not in rec
    assert "warning_signal_status" not in rec
    assert "SECRET UNVERIFIED" not in json.dumps(report)
    assert "650" not in json.dumps(report)  # raw history never included


def test_runner_never_persists_blocked_answer():
    report = run_benchmark(lambda c, f: success_result(publishable=False,
                                      answer="RAW UNVERIFIED DRAFT"))
    assert all(r["observed"]["public_answer"] == "" for r in report["cases"])
    assert "UNVERIFIED" not in json.dumps(report)


def test_runner_preserves_reason_codes_for_blocked_case():
    report = run_benchmark(lambda c, f: success_result(publishable=False))
    assert all(r["observed"]["reason_codes"] == ["POLICY_EVIDENCE_INSUFFICIENT"]
               for r in report["cases"])


def test_runner_catches_one_exception_then_continues():
    def execute(case, fixture):
        if case["id"] == "CF-003":
            raise RuntimeError("secret provider token = super-secret")
        return success_result()

    report = run_benchmark(execute)
    assert [r["case_id"] for r in report["cases"]] == [f"CF-{i:03}" for i in range(1, 7)]
    assert report["cases"][2]["observed"] is None
    assert report["cases"][2]["error_type"] == "RuntimeError"
    assert report["cases"][3]["observed"] is not None
    assert "super-secret" not in json.dumps(report)


@pytest.mark.parametrize("fault", [
    lambda result: [],
    lambda result: {k:v for k,v in result.items() if k != "publishable"},
    lambda result: {**result, "publishable": 1},
    lambda result: {**result, "tool_rounds": True},
    lambda result: {**result, "reason_codes": [15]},
    lambda result: {**result, "tool_evidence": [{}]},
])
def test_malformed_executor_result_is_recorded_as_error(fault):
    report = run_benchmark(lambda c, f: fault(success_result()))
    assert all(r["observed"] is None for r in report["cases"])
    assert all(r["error_type"] == "ValueError" for r in report["cases"])


def test_duplicate_policy_sources_deduplicated():
    evidence = [
        {"tool_name": "retrieve_policy", "result": [
            {"source": "POL-002_early_warning_thresholds.md"},
            {"source": "POL-002_early_warning_thresholds.md"},
            {"source": "not-a-policy"},
        ]},
        {"tool_name": "retrieve_policy", "result": [
            {"source": "POL-004_site_market_exception_rules.md"},
        ]},
    ]
    assert _policy_ids_from_tool_evidence(evidence) == ["POL-002", "POL-004"]


def test_policy_source_from_wrong_tool_not_counted():
    evidence = [{"tool_name": "get_history", "result": [
        {"source": "POL-002_early_warning_thresholds.md"}
    ]}]
    assert _policy_ids_from_tool_evidence(evidence) == []


def test_latency_clock_measured_in_milliseconds():
    tick = iter([1.0, 1.125] * 6)
    report = run_benchmark(lambda c, f: success_result(), clock=lambda: next(tick))
    assert all(r["elapsed_ms"] == 125.0 for r in report["cases"])


@pytest.mark.parametrize("name", ["", "with space", "../files", "\n", "a" * 65])
def test_bad_provider_id_fails_before_running(name):
    with pytest.raises(ValueError, match="provider label"):
        run_benchmark(lambda c, f: success_result(), provider_id=name)


def test_invalid_execution_mode_fails():
    with pytest.raises(ValueError, match="execution mode"):
        run_benchmark(lambda c, f: success_result(), execution_mode="magical")


def test_noncalls_executor_rejected():
    with pytest.raises(TypeError, match="executor"):
        run_benchmark(None)


def test_report_json_roundtrip_and_no_overwrite(tmp_path):
    report = run_benchmark(lambda c, f: success_result())
    saved = save_report(report, tmp_path / "results" / "run.json")
    assert json.loads(saved.read_text(encoding="utf-8")) == report
    with pytest.raises(FileExistsError):
        save_report(report, saved)


def test_run_scripted_case_uses_isolated_dispatch_and_trusted_context(monkeypatch):
    """Check the adapter's DB/network isolation without importing dependencies."""
    import src
    fixtures_data = fixtures()
    case = manifest()["cases"][0]
    fixture = fixtures_data["fixtures"][0]
    calls = []
    fake = types.ModuleType("src.agent")

    def must_not_run(**kwargs):
        raise AssertionError("real database dispatch was reached")

    fake.ALL_TOOL_DISPATCH = {"get_history": must_not_run, "get_market_average": must_not_run,
                              "retrieve_policy": must_not_run}

    def simulated_run(question, provider, *, publication_mode, claim_scope, verbose, **kwargs):
        assert set(fake.ALL_TOOL_DISPATCH) == {"get_history", "retrieve_policy"}
        assert kwargs == {"expected_account_id": 81000001, "expected_market": 1,
                          "expected_period": "1991-12-01"}
        output = provider.chat(messages=[], tools=[])
        assert [t.name for t in output.tool_calls] == ["get_history", "retrieve_policy"]
        rows = fake.ALL_TOOL_DISPATCH["get_history"](account_id=81000001)
        docs = fake.ALL_TOOL_DISPATCH["retrieve_policy"](query="risk")
        assert rows[0]["score"] == 286
        assert docs[0]["source"].startswith("POL-002")
        calls.append(True)
        return success_result()

    fake.run_agent = simulated_run
    with patch.dict(sys.modules, {"src.agent": fake}), patch.object(src, "agent", fake, create=True):
        result = run_scripted_case(case, fixture)
    assert result["publishable"]
    assert calls == [True]
    assert fake.ALL_TOOL_DISPATCH["get_history"] is must_not_run


def test_scripted_case_refuses_wrong_account_before_agent_import():
    c = manifest()["cases"][0]
    f = fixtures()["fixtures"][0]
    f["account_id"] = 1234
    with pytest.raises(FixtureValidationError, match="disagree"):
        run_scripted_case(c, f)


@pytest.mark.parametrize("changes", [
    {"status": "incomplete"},
    {"next_action": "manual_review"},
    {"reason_codes": ["UNVERIFIED"]},
    {"completed": False},
])
def test_inconsistent_published_result_is_rejected(changes):
    with pytest.raises(ValueError, match="Inconsistent published"):
        _normal_observation({**success_result(), **changes})


def test_inconsistent_blocked_publish_action_is_rejected():
    with pytest.raises(ValueError, match="Inconsistent blocked"):
        _normal_observation({**success_result(publishable=False), "next_action": "publish"})


def test_reports_include_stable_manifest_fingerprints():
    b, f = manifest(), fixtures()
    first = run_benchmark(lambda c, x: success_result(), benchmark=b, fixtures=f)
    second = run_benchmark(lambda c, x: success_result(), benchmark=b, fixtures=f)
    assert first["benchmark_sha256"] == second["benchmark_sha256"]
    assert first["fixture_sha256"] == second["fixture_sha256"]
    assert len(first["benchmark_sha256"]) == 64
    f["fixtures"][0]["scores"][0] -= 1
    changed = run_benchmark(lambda c, x: success_result(), benchmark=b, fixtures=f)
    assert changed["fixture_sha256"] != first["fixture_sha256"]
