"""Module 6.3 — deterministic benchmark scoring of Module 6.2 run reports.

This scorer does not execute an LLM, replay policies, or validate score facts.
It compares observed publication-gate outcomes and retrieval identifiers with
frozen benchmark expectations. It refuses incompatible or malformed reports.

Python 3.11+; standard library only.
"""

from __future__ import annotations

import math
import re
from statistics import mean
from typing import Any

from src.evaluation_dataset import load_benchmark, validate_benchmark
from src.evaluation_runner import (
    _fingerprint, load_fixtures, validate_fixtures,
)


class EvaluationScoreError(ValueError):
    """The run report is not compatible with the frozen benchmark contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationScoreError(message)


def _string_list(value: Any, label: str, *, unique: bool = False) -> None:
    _require(type(value) is list and all(type(x) is str and x.strip() for x in value),
             f"{label} must be a list of nonblank strings")
    if unique:
        _require(len(value) == len(set(value)), f"{label} contains duplicates")


def validate_run_report(report: Any, benchmark: dict, fixtures: dict) -> dict:
    """Validate the exact Module 6.2 report format before calculating metrics.

    Fingerprints ensure a frozen case list is scored with its own fixture set;
    they do not authenticate the provenance of the execution itself.
    """
    validate_benchmark(benchmark)
    validate_fixtures(fixtures, benchmark)
    _require(type(report) is dict and set(report) == {
        "benchmark_id", "benchmark_schema_version", "fixture_set_id",
        "benchmark_sha256", "fixture_sha256", "runner_version",
        "provider_id", "execution_mode", "cases",
    }, "Invalid Module 6.2 report schema")
    _require(report["benchmark_id"] == benchmark["benchmark_id"],
             "Benchmark ID mismatch")
    _require(type(report["benchmark_schema_version"]) is int
             and report["benchmark_schema_version"] == benchmark["schema_version"],
             "Benchmark schema version mismatch")
    _require(report["fixture_set_id"] == fixtures["fixture_set_id"],
             "Fixture set ID mismatch")
    _require(type(report["benchmark_sha256"]) is str
             and report["benchmark_sha256"] == _fingerprint(benchmark),
             "Benchmark fingerprint mismatch")
    _require(type(report["fixture_sha256"]) is str
             and report["fixture_sha256"] == _fingerprint(fixtures),
             "Fixture fingerprint mismatch")
    _require(report["runner_version"] == "6.2", "Unsupported runner version")
    _require(type(report["provider_id"]) is str
             and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}",
                              report["provider_id"]) is not None,
             "Invalid provider ID")
    _require(report["execution_mode"] in ("scripted_offline", "provider_adapter"),
             "Invalid execution mode")
    rows = report["cases"]
    expected_cases = benchmark["cases"]
    _require(type(rows) is list and len(rows) == len(expected_cases),
             "Run report must include every benchmark case exactly once")

    for index, (row, case) in enumerate(zip(rows, expected_cases)):
        label = f"cases[{index}]"
        _require(type(row) is dict and set(row) == {
            "case_id", "scenario_id", "elapsed_ms", "observed", "error_type",
        }, f"{label}: invalid record schema")
        _require(row["case_id"] == case["id"]
                 and row["scenario_id"] == case["scenario_id"],
                 f"{label}: missing, reordered, or mismatched case")
        elapsed = row["elapsed_ms"]
        _require(type(elapsed) in (float, int) and math.isfinite(elapsed)
                 and elapsed >= 0, f"{label}: invalid latency")
        observation, error = row["observed"], row["error_type"]
        if observation is None:
            _require(type(error) is str
                     and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", error) is not None,
                     f"{label}: missing valid execution-error type")
            continue
        _require(error is None, f"{label}: error and observation cannot coexist")
        _require(type(observation) is dict and set(observation) == {
            "status", "publishable", "next_action", "reason_codes", "completed",
            "tool_rounds", "tool_names", "retrieved_policy_ids", "public_answer",
        }, f"{label}: invalid observation schema")
        _require(type(observation["status"]) is str
                 and observation["status"] in ("passed", "failed", "incomplete", "not_applicable"),
                 f"{label}: invalid status")
        _require(type(observation["publishable"]) is bool
                 and type(observation["completed"]) is bool,
                 f"{label}: invalid boolean flags")
        _require(type(observation["next_action"]) is str and bool(observation["next_action"].strip()),
                 f"{label}: invalid next_action")
        _require(type(observation["tool_rounds"]) is int and observation["tool_rounds"] >= 0,
                 f"{label}: invalid tool_rounds")
        _string_list(observation["reason_codes"], f"{label}.reason_codes", unique=True)
        _string_list(observation["tool_names"], f"{label}.tool_names")
        _string_list(observation["retrieved_policy_ids"], f"{label}.retrieved_policy_ids", unique=True)
        _require(all(re.fullmatch(r"POL-\d{3}", x) for x in observation["retrieved_policy_ids"]),
                 f"{label}: invalid policy identifier")
        _require(type(observation["public_answer"]) is str,
                 f"{label}: invalid public answer")
        if observation["publishable"]:
            _require(observation["status"] == "passed"
                     and observation["next_action"] == "publish"
                     and observation["completed"] and not observation["reason_codes"],
                     f"{label}: inconsistent publishable observation")
        else:
            _require(observation["next_action"] != "publish"
                     and not observation["public_answer"],
                     f"{label}: unpublishable observation must not expose draft text")
    return report


def _phrase_normalize(value: str) -> str:
    """Case-insensitive, whitespace-collapsed literal substring check."""
    return " ".join(value.casefold().split())


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def score_report(
    run_report: dict,
    *,
    benchmark: dict | None = None,
    fixtures: dict | None = None,
) -> dict:
    """Score recorded Module 6.2 outcomes against frozen expected labels.

    Negative tests are *successful* if a block is the expected publication
    decision and its action, reasons, and retrieval checks agree. Executor
    errors are never credited as correct blocks. No provider/LLM calls occur.

    This score is not a factual review of policy conclusions: warning status
    and event count are not recorded in the Module 6.2 observation schema.
    """
    checked = load_benchmark() if benchmark is None else validate_benchmark(benchmark)
    source = (load_fixtures(benchmark=checked) if fixtures is None
              else validate_fixtures(fixtures, checked))
    validate_run_report(run_report, checked, source)

    details: list[dict] = []
    policy_found_total = policy_required_total = 0
    phrase_found_total = phrase_required_total = 0
    expected_publish_total = expected_block_total = 0
    correct_publishes = correct_blocks = false_publishes = false_blocks = 0

    for case, run_row in zip(checked["cases"], run_report["cases"]):
        expected = case["expected"]
        observed = run_row["observed"]
        expected_pub = expected["publishable"]
        actual_pub = observed["publishable"] if observed is not None else None
        publication_match = observed is not None and actual_pub == expected_pub
        action_match = (observed is not None
                        and observed["next_action"] == expected["next_action"])
        reason_match = (observed is not None
                        and set(observed["reason_codes"]) == set(expected["reason_codes"]))

        needed_policies = expected["required_policy_ids"]
        found_policies = observed["retrieved_policy_ids"] if observed is not None else []
        found_set = set(found_policies)
        matched_policies = [p for p in needed_policies if p in found_set]
        missing_policies = [p for p in needed_policies if p not in found_set]
        policy_found_total += len(matched_policies)
        policy_required_total += len(needed_policies)

        needed_phrases = expected["public_phrases"]
        # Never give phrase credit to an unpublished or invalid response.
        rendered = (_phrase_normalize(observed["public_answer"])
                    if observed is not None and actual_pub else "")
        matched_phrases = [p for p in needed_phrases if _phrase_normalize(p) in rendered]
        missing_phrases = [p for p in needed_phrases if p not in matched_phrases]
        phrase_found_total += len(matched_phrases)
        phrase_required_total += len(needed_phrases)

        if expected_pub:
            expected_publish_total += 1
            if actual_pub is True:
                correct_publishes += 1
            elif actual_pub is False:
                false_blocks += 1
        else:
            expected_block_total += 1
            if actual_pub is False:
                correct_blocks += 1
            elif actual_pub is True:
                false_publishes += 1

        passed = (observed is not None and publication_match and action_match
                  and reason_match and not missing_policies and not missing_phrases)
        details.append({
            "case_id": case["id"],
            "scenario_id": case["scenario_id"],
            "test_type": "positive" if expected_pub else "negative",
            "expected_publishable": expected_pub,
            "observed_publishable": actual_pub,
            "expected_next_action": expected["next_action"],
            "observed_next_action": observed["next_action"] if observed else None,
            "publication_match": publication_match,
            "action_match": action_match,
            "reason_codes_match": reason_match,
            "missing_required_policy_ids": missing_policies,
            "required_policy_retrieval_coverage": _ratio(len(matched_policies), len(needed_policies)),
            "missing_public_phrases": missing_phrases,
            "public_phrase_coverage": _ratio(len(matched_phrases), len(needed_phrases)),
            "execution_error_type": run_row["error_type"],
            "elapsed_ms": run_row["elapsed_ms"],
            "passed": passed,
        })

    n = len(details)
    success_times = [row["elapsed_ms"] for row in details if row["execution_error_type"] is None]
    # Nearest-rank p95, among non-error runs, not network-only latency.
    sorted_times = sorted(success_times)
    p95 = sorted_times[math.ceil(0.95 * len(sorted_times)) - 1] if sorted_times else None
    mean_ms = round(mean(success_times), 3) if success_times else None
    counts = {
        "cases_total": n,
        "execution_success_count": len(success_times),
        "execution_error_count": n - len(success_times),
        "cases_passed_all_checks": sum(row["passed"] for row in details),
        "expected_publish_cases": expected_publish_total,
        "correct_publishes": correct_publishes,
        "false_blocks": false_blocks,
        "expected_block_cases": expected_block_total,
        "correct_blocks": correct_blocks,
        "false_publishes": false_publishes,
        "publication_decision_accuracy": _ratio(sum(row["publication_match"] for row in details), n),
        "next_action_accuracy": _ratio(sum(row["action_match"] for row in details), n),
        "reason_code_exact_match_rate": _ratio(sum(row["reason_codes_match"] for row in details), n),
        "all_checks_pass_rate": _ratio(sum(row["passed"] for row in details), n),
        "positive_case_publish_recall": _ratio(correct_publishes, expected_publish_total),
        "negative_test_block_recall": _ratio(correct_blocks, expected_block_total),
        "negative_test_false_publication_rate": _ratio(false_publishes, expected_block_total),
        "required_policy_retrieval_coverage": _ratio(policy_found_total, policy_required_total),
        "public_phrase_coverage": _ratio(phrase_found_total, phrase_required_total),
        "mean_successful_run_elapsed_ms": mean_ms,
        "p95_successful_run_elapsed_ms": p95,
    }
    return {
        "evaluation_id": "scorecopilot_metrics_v1",
        "scorer_version": "6.3",
        "benchmark_id": run_report["benchmark_id"],
        "benchmark_sha256": run_report["benchmark_sha256"],
        "fixture_sha256": run_report["fixture_sha256"],
        "provider_id": run_report["provider_id"],
        "execution_mode": run_report["execution_mode"],
        "metrics": counts,
        "cases": details,
    }
