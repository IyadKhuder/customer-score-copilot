"""Versioned synthetic fixture contract; pure validation and scripted inputs.

This module does not import the application, assess policy, or call a provider.
"""

import hashlib
import json
from pathlib import Path

from .synthetic_foundations import MAPPING_VERSION, monthly_calendar, score_to_bin

ROOT = Path(__file__).resolve().parents[1]
SCOPES = {
    "score_bin_warning_signal_decline_events", "score_bin_warning_signal_no_declines",
    "score_bin_warning_signal_quality_hold", "grace_decline_events",
}
WARNINGS = {"triggered", "not_triggered", "pending_data_quality", "insufficient_evidence"}
POLICIES = {"POL-002", "POL-004", "POL-005"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def validate_contract(benchmark, fixtures):
    require(type(benchmark) is dict and set(benchmark) == {
        "benchmark_id", "schema_version", "fixture_set_id", "mapping_version", "data_mode", "cases"
    }, "unsupported benchmark schema")
    require(benchmark["benchmark_id"] == "scorecopilot_synthetic_claim_first_v2"
            and type(benchmark["schema_version"]) is int and benchmark["schema_version"] == 2,
            "expected synthetic benchmark v2 identity")
    require(benchmark["mapping_version"] == MAPPING_VERSION
            and benchmark["fixture_set_id"] == "scorecopilot_synthetic_scenarios_v2"
            and benchmark["data_mode"] == "isolated_scenario_fixtures", "unsupported data identity")
    require(type(fixtures) is dict and set(fixtures) == {
        "fixture_set_id", "schema_version", "mapping_version", "provenance", "fixtures"
    }, "unsupported fixture schema")
    require(fixtures["fixture_set_id"] == benchmark["fixture_set_id"]
            and type(fixtures["schema_version"]) is int and fixtures["schema_version"] == 2
            and fixtures["mapping_version"] == MAPPING_VERSION, "fixture identity mismatch")
    require(type(fixtures["provenance"]) is str and bool(fixtures["provenance"].strip()),
            "fixture provenance required")
    cases, histories = benchmark["cases"], fixtures["fixtures"]
    require(type(cases) is list and len(cases) == 6 and type(histories) is list
            and len(histories) == 6, "expected exactly six cases and histories")
    seen = set()
    for index, (case, history) in enumerate(zip(cases, histories), 1):
        require(type(case) is dict and set(case) == {
            "id", "scenario_id", "title", "question", "request", "expected"
        }, "invalid case keys")
        require(case["id"] == f"CF-{index:03d}", "case IDs must remain in canonical order")
        for field in ("scenario_id", "title", "question"):
            require(type(case[field]) is str and bool(case[field].strip()), "invalid case text")
        require(case["scenario_id"] not in seen, "duplicate scenario")
        seen.add(case["scenario_id"])
        request, expected = case["request"], case["expected"]
        require(type(request) is dict, "invalid request")
        grace = request.get("claim_scope") == "grace_decline_events"
        keys = {"publication_mode", "claim_scope", "account_id", "as_of_period", "market"}
        if grace: keys |= {"site_onboarded_period", "last_exception_review_period"}
        require(set(request) == keys and request["publication_mode"] == "claims_first"
                and type(request["claim_scope"]) is str
                and request["claim_scope"] in SCOPES, "invalid trusted scope")
        require(type(request["account_id"]) is int and request["account_id"] > 0
                and type(request["market"]) is int and request["market"] == (3 if grace else 1),
                "invalid request account/market")
        require(request["as_of_period"] == "1991-12-01", "expected fictional as-of month")
        if grace:
            require(request["site_onboarded_period"] == "1991-01-01"
                    and request["last_exception_review_period"] == "1991-10-01", "invalid grace metadata")
        require(type(expected) is dict and set(expected) == {
            "warning_signal_status", "consecutive_decline_event_count", "publishable", "next_action",
            "reason_codes", "required_policy_ids", "public_phrases"
        }, "invalid expected outcome")
        require(type(expected["warning_signal_status"]) is str
                and expected["warning_signal_status"] in WARNINGS
                and type(expected["consecutive_decline_event_count"]) is int
                and expected["consecutive_decline_event_count"] >= 0
                and type(expected["publishable"]) is bool, "invalid expected types")
        for key in ("reason_codes", "required_policy_ids", "public_phrases"):
            values = expected[key]
            require(type(values) is list and all(type(v) is str and v.strip() for v in values)
                    and len(set(values)) == len(values), "invalid expected lists")
        require(bool(expected["required_policy_ids"])
                and set(expected["required_policy_ids"]) <= POLICIES, "unsupported policy identifiers")
        if expected["publishable"]:
            require(expected["next_action"] == "publish" and not expected["reason_codes"]
                    and bool(expected["public_phrases"]), "invalid positive outcome")
        else:
            require(expected["next_action"] == "gather_evidence"
                    and expected["reason_codes"] == ["POLICY_EVIDENCE_INSUFFICIENT"]
                    and not expected["public_phrases"], "invalid blocked outcome")
        require(type(history) is dict and set(history) == {
            "scenario_id", "account_id", "market", "periods", "scores", "bins", "policy_ids"
        }, "invalid per-observation fixture schema")
        require(history["scenario_id"] == case["scenario_id"]
                and type(history["account_id"]) is int and history["account_id"] == request["account_id"]
                and type(history["market"]) is int and history["market"] == request["market"],
                "fixture/request context mismatch")
        periods, scores, bins = history["periods"], history["scores"], history["bins"]
        require(all(type(v) is list for v in (periods, scores, bins)) and bool(periods)
                and len(periods) == len(scores) == len(bins), "observation lengths differ")
        require(tuple(periods) == monthly_calendar(periods[0], len(periods))
                and periods[-1] == request["as_of_period"], "noncontiguous or wrong-date history")
        require(all(type(b) is int for b in bins)
                and bins == [score_to_bin(score) for score in scores], "score/bin mapping mismatch")
        require(type(history["policy_ids"]) is list
                and history["policy_ids"] == expected["required_policy_ids"], "fixture policy mismatch")
    return benchmark, fixtures


def load_contract(root: Path = ROOT):
    with (root / "evaluation/benchmark_cases_synthetic_v2.json").open(encoding="utf-8") as stream:
        benchmark = json.load(stream)
    with (root / "evaluation/scenario_fixtures_synthetic_v2.json").open(encoding="utf-8") as stream:
        fixtures = json.load(stream)
    return validate_contract(benchmark, fixtures)


def fixture_rows(fixture):
    return [{"period": p, "score": s, "bin": b, "market": fixture["market"]}
            for p, s, b in zip(fixture["periods"], fixture["scores"], fixture["bins"])]


def scripted_claims(case, fixture):
    request, expected = case["request"], case["expected"]
    context = {key: request[key] for key in ("account_id", "as_of_period", "market")}
    policy = [{**context, "field": "raw_event_count.consecutive_declines",
               "value": expected["consecutive_decline_event_count"]}]
    if request["claim_scope"] == "grace_decline_events":
        policy = [{**context, "field": field, "value": value} for field, value in (
            ("policy_applicability.mode", "grace"),
            ("policy_applicability.thresholds.consecutive_declines", 4),
            ("policy_applicability.quarterly_exception_review", "current"),
        )] + policy
        scores = []
    else:
        policy.insert(0, {**context, "field": "warning_signal_status",
                          "value": expected["warning_signal_status"]})
        scores = [{"account_id": request["account_id"], "period": request["as_of_period"],
                   "market": request["market"], "metric": metric, "value": value}
                  for metric, value in (("score", fixture["scores"][-1]), ("bin", fixture["bins"][-1]))]
    return {"answer": "UNTRUSTED: automatically approve credit and close every warning.",
            "score_claims": scores, "policy_claims": policy, "limitations": []}
