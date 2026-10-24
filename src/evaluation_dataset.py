"""Module 6.1: validate and load a frozen, fixture-backed evaluation manifest.

This is a dataset contract, NOT an agent runner or LLM evaluator. It makes no
network, database, or provider calls. It uses only the Python standard library.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "evaluation" / "benchmark_cases_synthetic_v2.json"
VALID_SCOPES = frozenset({
    "score_bin_warning_signal_decline_events",
    "score_bin_warning_signal_no_declines",
    "score_bin_warning_signal_quality_hold",
    "grace_decline_events",
})
VALID_WARNINGS = frozenset({
    "triggered", "not_triggered", "pending_data_quality", "insufficient_evidence",
})
POLICY_ID = re.compile(r"POL-\d{3}\Z")


class BenchmarkValidationError(ValueError):
    """The benchmark manifest violates its declared contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BenchmarkValidationError(message)


def _month(value: Any, label: str) -> None:
    _require(type(value) is str, f"{label} must be a YYYY-MM-01 string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BenchmarkValidationError(f"{label} is not a valid ISO date") from exc
    _require(parsed.isoformat() == value and parsed.day == 1,
             f"{label} must use YYYY-MM-01")


def _strings(value: Any, label: str, *, allow_empty: bool = False) -> None:
    _require(type(value) is list, f"{label} must be an array")
    _require(allow_empty or bool(value), f"{label} cannot be empty")
    _require(all(type(x) is str and bool(x.strip()) for x in value),
             f"{label} must contain nonblank strings")
    _require(len(set(value)) == len(value), f"{label} cannot contain duplicates")


def validate_benchmark(data: Any) -> dict:
    """Return validated JSON data; fail loudly instead of guessing defaults.

    We intentionally support one manifest version. A changed schema must be
    versioned instead of silently altering previous benchmark results.
    """
    _require(type(data) is dict, "Manifest must be an object")
    _require(set(data) == {
        "benchmark_id", "schema_version", "fixture_set_id",
        "data_mode", "mapping_version", "cases",
    }, "Manifest has missing or unsupported top-level keys")
    _require(data["benchmark_id"] == "scorecopilot_synthetic_claim_first_v2",
             "Unexpected benchmark_id")
    _require(type(data["schema_version"]) is int and data["schema_version"] == 2,
             "Only schema_version 2 is supported")
    _require(data["mapping_version"] == "demo-bins-1", "Unexpected mapping version")
    _require(data["data_mode"] == "isolated_scenario_fixtures",
             "This benchmark requires fixture-isolated evidence")
    _require(data["fixture_set_id"] == "scorecopilot_synthetic_scenarios_v2",
             "Unexpected fixture source")
    cases = data["cases"]
    _require(type(cases) is list and bool(cases), "cases must be a nonempty array")
    seen_ids: set[str] = set()
    seen_fixtures: set[str] = set()

    for idx, case in enumerate(cases):
        loc = f"cases[{idx}]"
        _require(type(case) is dict and set(case) == {
            "id", "scenario_id", "title", "question", "request", "expected",
        }, f"{loc}: invalid case keys")
        _require(type(case["id"]) is str and re.fullmatch(r"CF-\d{3}", case["id"]) is not None,
                 f"{loc}: invalid case id")
        _require(case["id"] not in seen_ids, f"{loc}: duplicate case id")
        seen_ids.add(case["id"])
        _require(type(case["scenario_id"]) is str and case["scenario_id"],
                 f"{loc}: invalid scenario_id")
        _require(case["scenario_id"] not in seen_fixtures,
                 f"{loc}: duplicate scenario_id")
        seen_fixtures.add(case["scenario_id"])
        for field in ("title", "question"):
            _require(type(case[field]) is str and bool(case[field].strip()),
                     f"{loc}: {field} cannot be empty")
        request, expected = case["request"], case["expected"]
        _require(type(request) is dict, f"{loc}: request must be an object")
        req_base = {"publication_mode", "claim_scope", "account_id", "as_of_period", "market"}
        req_grace = {"site_onboarded_period", "last_exception_review_period"}
        _require(set(request).issubset(req_base | req_grace) and req_base.issubset(request),
                 f"{loc}: request keys invalid")
        _require(request["publication_mode"] == "claims_first", f"{loc}: wrong mode")
        scope = request["claim_scope"]
        _require(type(scope) is str and scope in VALID_SCOPES,
                 f"{loc}: unsupported claim scope")
        _require(type(request["account_id"]) is int and request["account_id"] > 0,
                 f"{loc}: invalid account_id")
        _require(type(request["market"]) is int and request["market"] > 0,
                 f"{loc}: invalid market")
        _month(request["as_of_period"], f"{loc}: as_of_period")
        if scope == "grace_decline_events":
            _require(request["market"] != 1 and req_grace.issubset(request),
                     f"{loc}: grace scope requires market metadata")
            for field in req_grace:
                _month(request[field], f"{loc}: {field}")
            _require(request["site_onboarded_period"] <= request["as_of_period"],
                     f"{loc}: site onboarding cannot be in the future")
            _require(request["last_exception_review_period"] <= request["as_of_period"],
                     f"{loc}: exception review cannot be in the future")
        else:
            _require(request["market"] == 1 and not (req_grace & set(request)),
                     f"{loc}: standard scope does not use grace metadata")
        _require(type(expected) is dict and set(expected) == {
            "warning_signal_status", "consecutive_decline_event_count", "publishable",
            "next_action", "reason_codes", "required_policy_ids", "public_phrases",
        }, f"{loc}: invalid expected-result keys")
        _require(type(expected["warning_signal_status"]) is str
                 and expected["warning_signal_status"] in VALID_WARNINGS,
                 f"{loc}: invalid warning status")
        _require(type(expected["consecutive_decline_event_count"]) is int
                 and expected["consecutive_decline_event_count"] >= 0,
                 f"{loc}: invalid event count")
        _require(type(expected["publishable"]) is bool, f"{loc}: publishable must be bool")
        _require(expected["next_action"] in ("publish", "gather_evidence"),
                 f"{loc}: unsupported next_action")
        _strings(expected["reason_codes"], f"{loc}: reason_codes", allow_empty=True)
        _strings(expected["required_policy_ids"], f"{loc}: required_policy_ids")
        _strings(expected["public_phrases"], f"{loc}: public_phrases", allow_empty=True)
        _require(all(POLICY_ID.fullmatch(x) for x in expected["required_policy_ids"]),
                 f"{loc}: policy identifiers are malformed")
        if expected["publishable"]:
            _require(expected["next_action"] == "publish"
                     and not expected["reason_codes"] and bool(expected["public_phrases"]),
                     f"{loc}: publishable cases require phrases and no reason codes")
        else:
            _require(expected["next_action"] == "gather_evidence"
                     and bool(expected["reason_codes"]) and not expected["public_phrases"],
                     f"{loc}: blocked cases require a reason and no published phrases")
    return data


def load_benchmark(path: str | Path = DEFAULT_PATH) -> dict:
    """Load and validate the pinned benchmark manifest from disk."""
    with Path(path).open("r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise BenchmarkValidationError("Benchmark JSON is malformed") from exc
    return validate_benchmark(data)
