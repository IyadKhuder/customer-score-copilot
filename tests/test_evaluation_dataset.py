"""Module 6.1: offline dataset-contract tests (Python 3.11+)."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from src.evaluation_dataset import (
    BenchmarkValidationError,
    DEFAULT_PATH,
    VALID_SCOPES,
    load_benchmark,
    validate_benchmark,
)


def sample():
    return copy.deepcopy(load_benchmark())


def invalid(data, match):
    with pytest.raises(BenchmarkValidationError, match=match):
        validate_benchmark(data)


def test_manifest_loads_and_has_six_cases():
    benchmark = load_benchmark()
    assert benchmark["benchmark_id"] == "scorecopilot_synthetic_claim_first_v2"
    assert benchmark["mapping_version"] == "demo-bins-1"
    assert len(benchmark["cases"]) == 6


def test_case_ids_and_scenarios_are_unique():
    cases = sample()["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["scenario_id"] for c in cases}) == len(cases)


def test_all_scopes_are_supported():
    assert {c["request"]["claim_scope"] for c in sample()["cases"]} == VALID_SCOPES


def test_pinned_expected_outcomes():
    expected = {c["scenario_id"]: (c["expected"]["warning_signal_status"],
                                    c["expected"]["consecutive_decline_event_count"],
                                    c["expected"]["publishable"])
                for c in sample()["cases"]}
    assert expected == {
        "standard_trigger": ("triggered", 1, True),
        "standard_no_trigger": ("not_triggered", 0, True),
        "standard_quality_hold": ("pending_data_quality", 2, True),
        "insufficient_history": ("insufficient_evidence", 0, False),
        "grace_eligible": ("triggered", 1, True),
        "grace_quality_hold": ("pending_data_quality", 1, True),
    }


def test_fixture_references_exist_in_scenario_matrix():
    # AST inspection avoids executing imports, patching providers, or calling an LLM.
    root = DEFAULT_PATH.parent.parent
    source = (root / "tests" / "test_claim_first_scenario_matrix.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fixture_ids = {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "Scenario" and node.args
        and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is str
    }
    assert fixture_ids >= {case["scenario_id"] for case in sample()["cases"]}


def test_no_duplicate_id():
    data = sample()
    data["cases"][1]["id"] = data["cases"][0]["id"]
    invalid(data, "duplicate case id")


def test_no_duplicate_scenario():
    data = sample()
    data["cases"][1]["scenario_id"] = data["cases"][0]["scenario_id"]
    invalid(data, "duplicate scenario_id")


def test_wrong_schema_version():
    data = sample()
    data["schema_version"] = 99
    invalid(data, "schema_version")


def test_missing_case_key():
    data = sample()
    del data["cases"][0]["expected"]
    invalid(data, "invalid case keys")


def test_extra_manifest_key_rejected():
    data = sample()
    data["unknown"] = True
    invalid(data, "top-level keys")


def test_noniso_date_rejected():
    data = sample()
    data["cases"][0]["request"]["as_of_period"] = "April 2025"
    invalid(data, "ISO date")


def test_nonfirst_day_rejected():
    data = sample()
    data["cases"][0]["request"]["as_of_period"] = "2025-04-15"
    invalid(data, "YYYY-MM-01")


def test_boolean_account_id_rejected():
    data = sample()
    data["cases"][0]["request"]["account_id"] = True
    invalid(data, "account_id")


def test_bad_scope_rejected():
    data = sample()
    data["cases"][0]["request"]["claim_scope"] = "magic_llm_prose"
    invalid(data, "unsupported claim scope")


def test_grace_requires_dates():
    data = sample()
    del data["cases"][4]["request"]["site_onboarded_period"]
    invalid(data, "requires market metadata")


def test_standard_forbids_grace_metadata():
    data = sample()
    data["cases"][0]["request"]["site_onboarded_period"] = "2024-05-01"
    invalid(data, "standard scope")


def test_grace_onboarding_cannot_be_future():
    data = sample()
    data["cases"][4]["request"]["site_onboarded_period"] = "2025-05-01"
    invalid(data, "onboarding cannot be in the future")


def test_no_boolean_event_count():
    data = sample()
    data["cases"][0]["expected"]["consecutive_decline_event_count"] = True
    invalid(data, "event count")


def test_blocked_cases_require_reason():
    data = sample()
    data["cases"][3]["expected"]["reason_codes"] = []
    invalid(data, "blocked cases require a reason")


def test_publishable_case_cannot_have_reason():
    data = sample()
    data["cases"][0]["expected"]["reason_codes"] = ["FAIL"]
    invalid(data, "publishable cases")


def test_bad_policy_id_rejected():
    data = sample()
    data["cases"][0]["expected"]["required_policy_ids"] = ["policy-two"]
    invalid(data, "policy identifiers")


def test_negative_event_count_rejected():
    data = sample()
    data["cases"][0]["expected"]["consecutive_decline_event_count"] = -1
    invalid(data, "event count")


def test_manifest_can_be_loaded_from_explicit_path(tmp_path):
    copy_path = tmp_path / "benchmark.json"
    copy_path.write_text(json.dumps(sample()), encoding="utf-8")
    assert load_benchmark(copy_path) == sample()


def test_malformed_json_raises_contract_error(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"broken": ', encoding="utf-8")
    with pytest.raises(BenchmarkValidationError, match="malformed"):
        load_benchmark(broken)
