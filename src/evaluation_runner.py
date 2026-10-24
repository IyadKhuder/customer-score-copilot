"""Module 6.2 — serial, provider-neutral benchmark execution and observation.

Runs the versioned Module 6.1 cases against a caller-supplied executor. An
optional scripted executor exercises the real agent offline, with isolated
synthetic histories and policy passages. It is NOT a test of LLM capability.

Only sanitized result summaries are retained; tool evidence, provider prompts,
raw JSON drafts, and API credentials are not written to benchmark reports.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from unittest.mock import patch

from src.evaluation_dataset import load_benchmark, validate_benchmark

DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "evaluation" / "scenario_fixtures_synthetic_v2.json"
)
_POLICY_FILE = re.compile(r"^(POL-\d{3})_[a-z0-9_]+\.md$")

# Small, controlled offline passages from the original scenario matrix.
# These are simplified synthetic snippets, NOT an appraisal of their relevance.
_SCRIPTED_POLICY_DOCS = {
    "POL-002": {
        "source": "POL-002_early_warning_thresholds.md",
        "text": "Three consecutive declining months within the policy window.",
    },
    "POL-004": {
        "source": "POL-004_site_market_exception_rules.md",
        "text": "Grace markets have adjusted warning thresholds.",
    },
    "POL-005": {
        "source": "POL-005_data_quality_and_volatility_flags.md",
        "text": "Potential score artifacts require data-quality review.",
    },
}


class FixtureValidationError(ValueError):
    """Fixture file and benchmark request cannot be safely paired."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise FixtureValidationError(reason)


def _month(value: Any) -> int:
    _require(type(value) is str, "Fixture period must be a string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise FixtureValidationError("Invalid fixture period") from exc
    _require(parsed.isoformat() == value and parsed.day == 1,
             "Fixture periods must be canonical month starts")
    return parsed.year * 12 + parsed.month - 1


def validate_fixtures(data: Any, benchmark: dict) -> dict:
    """Validate self-contained fixture data and exact agreement with cases."""
    validate_benchmark(benchmark)
    _require(type(data) is dict and set(data) == {
        "fixture_set_id", "schema_version", "mapping_version", "provenance", "fixtures",
    }, "Invalid fixture manifest keys")
    _require(data["fixture_set_id"] == "scorecopilot_synthetic_scenarios_v2"
             and type(data["schema_version"]) is int and data["schema_version"] == 2,
             "Unsupported fixture version")
    _require(data["mapping_version"] == "demo-bins-1", "Unsupported mapping version")
    _require(type(data["provenance"]) is str and bool(data["provenance"].strip()),
             "Missing fixture description")
    fixtures = data["fixtures"]
    _require(type(fixtures) is list, "Fixtures must be an array")
    cases = {c["scenario_id"]: c for c in benchmark["cases"]}
    _require(len(fixtures) == len(cases), "Fixture count differs from benchmark")
    seen: set[str] = set()
    for f in fixtures:
        _require(type(f) is dict and set(f) == {
            "scenario_id", "account_id", "market", "bins", "periods", "scores", "policy_ids",
        }, "Invalid fixture keys")
        scenario_id = f["scenario_id"]
        _require(type(scenario_id) is str and scenario_id in cases
                 and scenario_id not in seen, "Unknown or duplicated fixture scenario_id")
        seen.add(scenario_id)
        case = cases[scenario_id]
        request = case["request"]
        _require(type(f["account_id"]) is int and f["account_id"] == request["account_id"]
                 and type(f["market"]) is int and f["market"] == request["market"],
                 "Fixture account/market does not match trusted request")
        _require(type(f["bins"]) is list and all(type(b) is int and 1 <= b <= 9 for b in f["bins"]),
                 "Invalid fixture risk bin")
        periods, scores = f["periods"], f["scores"]
        _require(type(periods) is list and type(scores) is list and bool(periods)
                 and len(periods) == len(scores) == len(f["bins"]), "Fixture periods/scores mismatch")
        months = [_month(p) for p in periods]
        _require(all(b == a + 1 for a, b in zip(months, months[1:])),
                 "Fixture history must be chronological and monthly")
        _require(periods[-1] == request["as_of_period"],
                 "Fixture must end on the requested as-of month")
        _require(all(type(s) is int and 0 <= s <= 449 for s in scores),
                 "Invalid fixture scores")
        _require(f["bins"] == [1 + s // 50 for s in scores], "Fixture score/bin mapping mismatch")
        ids = f["policy_ids"]
        _require(type(ids) is list and bool(ids) and len(ids) == len(set(ids))
                 and all(type(x) is str and x in _SCRIPTED_POLICY_DOCS for x in ids),
                 "Unsupported fixture policy memo")
        _require(set(ids) == set(case["expected"]["required_policy_ids"]),
                 "Fixture policy sources disagree with benchmark case")
    return data


def load_fixtures(path: str | Path = DEFAULT_FIXTURE_PATH,
                  *, benchmark: dict | None = None) -> dict:
    """Read fixtures from a known file, never from the customer's SQLite DB."""
    validated_benchmark = load_benchmark() if benchmark is None else validate_benchmark(benchmark)
    with Path(path).open("r", encoding="utf-8") as stream:
        try:
            data = json.load(stream)
        except json.JSONDecodeError as exc:
            raise FixtureValidationError("Malformed fixture JSON") from exc
    return validate_fixtures(data, validated_benchmark)


def _policy_ids_from_tool_evidence(value: Any) -> list[str]:
    """Report observed retrieved sources (not expected/source coverage scores)."""
    found: set[str] = set()
    if type(value) is not list:
        return []
    for item in value:
        if type(item) is not dict or item.get("tool_name") != "retrieve_policy":
            continue
        passages = item.get("result")
        if type(passages) is not list:
            continue
        for passage in passages:
            if type(passage) is dict and type(passage.get("source")) is str:
                match = _POLICY_FILE.fullmatch(passage["source"])
                if match:
                    found.add(match.group(1))
    return sorted(found)


def _normal_observation(response: Any) -> dict:
    """Validate the agent result surface; never serialize an unverified draft."""
    if type(response) is not dict:
        raise ValueError("Executor must return an agent result object")
    required = {
        "answer", "status", "publishable", "next_action", "reason_codes",
        "completed", "tool_rounds", "tool_evidence",
    }
    if not required.issubset(response):
        raise ValueError("Agent result is missing required keys")
    if (type(response["answer"]) is not str
            or type(response["status"]) is not str
            or type(response["publishable"]) is not bool
            or type(response["next_action"]) is not str
            or type(response["completed"]) is not bool
            or type(response["tool_rounds"]) is not int
            or response["tool_rounds"] < 0
            or type(response["reason_codes"]) is not list
            or not all(type(x) is str for x in response["reason_codes"])
            or type(response["tool_evidence"]) is not list):
        raise ValueError("Agent result fields have invalid types")
    # Reject internal contradictions rather than recording them as valid gates.
    if response["publishable"] and (
        response["status"] != "passed" or response["next_action"] != "publish"
        or response["reason_codes"] or not response["completed"]
    ):
        raise ValueError("Inconsistent published agent outcome")
    if not response["publishable"] and response["next_action"] == "publish":
        raise ValueError("Inconsistent blocked agent outcome")
    evidence = response["tool_evidence"]
    if not all(type(entry) is dict and type(entry.get("tool_name")) is str
               for entry in evidence):
        raise ValueError("Tool evidence is malformed")
    # Never persist raw evidence (scores or source text) or unverified model drafts.
    # 'answer' is safe to persist ONLY when the publication gate marked it public.
    return {
        "status": response["status"],
        "publishable": response["publishable"],
        "next_action": response["next_action"],
        "reason_codes": response["reason_codes"][:],
        "completed": response["completed"],
        "tool_rounds": response["tool_rounds"],
        "tool_names": [entry["tool_name"] for entry in evidence],
        "retrieved_policy_ids": _policy_ids_from_tool_evidence(evidence),
        "public_answer": response["answer"] if response["publishable"] else "",
    }


def _fingerprint(data: dict) -> str:
    """Stable SHA-256 for the validated JSON contract, independent of spacing."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_benchmark(
    executor: Callable[[dict, dict], dict], *,
    benchmark: dict | None = None,
    fixtures: dict | None = None,
    provider_id: str = "scripted_offline",
    execution_mode: str = "scripted_offline",
    clock: Callable[[], float] = perf_counter,
) -> dict:
    """Execute all cases serially; record observations, NOT benchmark scores.

    The provided executor accepts (case, fixture) and returns the normal agent
    result. Future providers can adapt this interface without changing the
    loop. Executors must isolate data and respect the caller's request context.
    Exceptions are recorded per-case without leaking arbitrary error strings.
    """
    checked = load_benchmark() if benchmark is None else validate_benchmark(benchmark)
    source = load_fixtures(benchmark=checked) if fixtures is None else validate_fixtures(fixtures, checked)
    if not callable(executor):
        raise TypeError("executor must be callable")
    if (type(provider_id) is not str or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", provider_id)
            or execution_mode not in ("scripted_offline", "provider_adapter")):
        raise ValueError("Invalid provider label or execution mode")
    if not callable(clock):
        raise TypeError("clock must be callable")
    by_name = {f["scenario_id"]: f for f in source["fixtures"]}
    records = []
    for case in checked["cases"]:
        started = clock()
        try:
            response = executor(copy.deepcopy(case), copy.deepcopy(by_name[case["scenario_id"]]))
            observed = _normal_observation(response)
            error_type = None
        except Exception as exc:
            observed = None
            error_type = type(exc).__name__  # do not store exception messages/secrets
        elapsed = clock() - started
        records.append({
            "case_id": case["id"],
            "scenario_id": case["scenario_id"],
            "elapsed_ms": round(max(0.0, elapsed) * 1000, 3),
            "observed": observed,
            "error_type": error_type,
        })
    return {
        "benchmark_id": checked["benchmark_id"],
        "benchmark_schema_version": checked["schema_version"],
        "fixture_set_id": source["fixture_set_id"],
        "benchmark_sha256": _fingerprint(checked),
        "fixture_sha256": _fingerprint(source),
        "runner_version": "6.2",
        "provider_id": provider_id,
        "execution_mode": execution_mode,
        "cases": records,
    }


def save_report(report: dict, path: str | Path) -> Path:
    """Write JSON reproducibly; never automatically overwrite an earlier run."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    return destination


@dataclass(frozen=True)
class _ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class _Reply:
    text: str = ""
    tool_calls: list[_ToolCall] = field(default_factory=list)


class _ScriptedProvider:
    """Supplies predeclared claims; NEVER use its runs as LLM accuracy data."""

    def __init__(self, answer: dict, account: int):
        self._answer = json.dumps(answer)
        self._account = account

    def chat(self, *, messages, tools):
        return _Reply(tool_calls=[
            _ToolCall("fixture-history", "get_history", {"account_id": self._account}),
            _ToolCall("fixture-policy", "retrieve_policy", {"query": "synthetic risk memo"}),
        ])

    def continue_with_tool_results(self, *, assistant_response, tool_results, tools):
        return _Reply(text=self._answer)


def _scripted_claims(case: dict, fixture: dict) -> dict:
    """Reference transcript uses pinned labels, not independent model output."""
    request, expected = case["request"], case["expected"]
    account, market, period = request["account_id"], request["market"], request["as_of_period"]
    base = {"account_id": account, "as_of_period": period, "market": market}
    if request["claim_scope"] == "grace_decline_events":
        policy = [
            {**base, "field": "policy_applicability.mode", "value": "grace"},
            {**base, "field": "policy_applicability.thresholds.consecutive_declines", "value": 4},
            {**base, "field": "policy_applicability.quarterly_exception_review", "value": "current"},
            {**base, "field": "raw_event_count.consecutive_declines",
             "value": expected["consecutive_decline_event_count"]},
        ]
        scores = []
    else:
        policy = [
            {**base, "field": "warning_signal_status", "value": expected["warning_signal_status"]},
            {**base, "field": "raw_event_count.consecutive_declines",
             "value": expected["consecutive_decline_event_count"]},
        ]
        scores = [
            {"account_id": account, "period": period, "metric": name,
             "value": value, "market": market}
            for name, value in (("score", fixture["scores"][-1]), ("bin", fixture["bins"][-1]))
        ]
    return {
        "answer": "UNTRUSTED scripted provider draft: approve credit automatically",
        "score_claims": scores,
        "policy_claims": policy,
        "limitations": [],
    }


def run_scripted_case(case: dict, fixture: dict) -> dict:
    """Offline smoke-test the real agent with isolated score/policy tools.

    NOTE: patch.dict temporarily changes an agent-global dispatch table. This
    adapter is strictly sequential; it must NOT run concurrently with live
    traffic or other in-process agent calls. The general runner has no such
    constraint when supplied an isolated executor by the caller.
    """
    req = case["request"]
    if (fixture["scenario_id"] != case["scenario_id"]
            or fixture["account_id"] != req["account_id"]
            or fixture["market"] != req["market"]
            or fixture["periods"][-1] != req["as_of_period"]):
        raise FixtureValidationError("Case and fixture disagree")
    from src import agent  # lazy import after validating context

    rows = [
        {"period": period, "score": score, "bin": bin_number, "market": req["market"]}
        for period, score, bin_number in zip(fixture["periods"], fixture["scores"], fixture["bins"])
    ]
    documents = [copy.deepcopy(_SCRIPTED_POLICY_DOCS[key]) for key in fixture["policy_ids"]]
    # CLEAR ALL real dispatch entries to prevent accidental DB/network calls.
    # An additional or incorrect tool name fails closed in agent.py.
    isolated_dispatch = {
        "get_history": lambda account_id: copy.deepcopy(rows) if account_id == req["account_id"] else [],
        "retrieve_policy": lambda **kwargs: copy.deepcopy(documents),
    }
    kwargs = {
        "expected_account_id": req["account_id"],
        "expected_period": req["as_of_period"],
        "expected_market": req["market"],
    }
    if req["claim_scope"] == "grace_decline_events":
        kwargs["site_onboarded_period"] = req["site_onboarded_period"]
        kwargs["last_exception_review_period"] = req["last_exception_review_period"]
    provider = _ScriptedProvider(_scripted_claims(case, fixture), req["account_id"])
    with patch.dict(agent.ALL_TOOL_DISPATCH, isolated_dispatch, clear=True):
        return agent.run_agent(
            case["question"], provider, publication_mode=req["publication_mode"],
            claim_scope=req["claim_scope"], verbose=False, **kwargs,
        )
