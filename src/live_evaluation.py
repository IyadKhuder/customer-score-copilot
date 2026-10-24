"""Module 6.4 — opt-in real-LLM benchmark adapter with isolated fixture tools.

This executes the LIVE provider through the existing claim-first agent and gate,
while preventing access to the application's SQLite tools. The policy passages
are intentionally fixed synthetic snippets, NOT a live TF-IDF retrieval test.

The agent tool registry is temporarily patched: use only in a single-threaded,
standalone benchmark process; never alongside other in-process agent traffic.
No provider credentials, prompts, raw replies, or unpublished drafts are saved.
"""
from __future__ import annotations

import copy
import re
from typing import Any
from unittest.mock import patch

from src.evaluation_runner import (
    FixtureValidationError, _SCRIPTED_POLICY_DOCS, run_benchmark,
)
from src.policy_passage_variants import (
    POLICY_PASSAGE_VARIANTS, policy_passages,
)
from src.policy_assist_experiment import (
    ASSIST_TOOL_SCHEMA, ASSIST_PROMPT_APPENDIX, build_assist_dispatch,
)
from src.policy_assist_suite import (
    SUITE_TOOL_SCHEMA, SUITE_PROMPT_APPENDICES, build_suite_assist_dispatch,
)

_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def _check_client(client: Any) -> None:
    if not callable(getattr(client, "chat", None)) or not callable(
        getattr(client, "continue_with_tool_results", None)
    ):
        raise TypeError("Provider adapter must implement chat and continue_with_tool_results")


def _check_round_limit(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 8:
        raise ValueError("max_tool_rounds must be an integer between 1 and 8")


def _check_pair(case: dict, fixture: dict) -> None:
    if type(case) is not dict or type(fixture) is not dict:
        raise FixtureValidationError("Benchmark case and fixture must be objects")
    req = case.get("request")
    if type(req) is not dict:
        raise FixtureValidationError("Missing benchmark request")
    if (fixture.get("scenario_id") != case.get("scenario_id")
            or fixture.get("account_id") != req.get("account_id")
            or fixture.get("market") != req.get("market")
            or not fixture.get("periods")
            or fixture["periods"][-1] != req.get("as_of_period")):
        raise FixtureValidationError("Benchmark request and synthetic history disagree")
    if (req.get("publication_mode") != "claims_first"
            or not isinstance(req.get("claim_scope"), str)):
        raise FixtureValidationError("Unsupported benchmark request")
    ids = fixture.get("policy_ids")
    if type(ids) is not list or any(x not in _SCRIPTED_POLICY_DOCS for x in ids):
        raise FixtureValidationError("Unsupported synthetic policy sources")
    if (type(fixture.get("scores")) is not list
            or len(fixture["periods"]) != len(fixture["scores"])):
        raise FixtureValidationError("Invalid synthetic score history")


def run_live_case(
    case: dict,
    fixture: dict,
    client: Any,
    *,
    max_tool_rounds: int = 4,
    policy_passage_variant: str = "baseline",
    policy_tool_assist: bool = False,
    policy_tool_suite: bool = False,
) -> dict:
    """Call the real model, but supply *only* isolated benchmark tool responses.

    `client` comes from the caller's existing Gemini/Anthropic adapter. A
    malicious or mistaken tool name cannot invoke a production dispatch entry.
    Returns the existing agent's unchanged result (with a private audit); the
    Module 6.2 runner sanitizes it before persisting a report.
    """
    _check_client(client)
    _check_round_limit(max_tool_rounds)
    if policy_passage_variant not in POLICY_PASSAGE_VARIANTS:
        raise ValueError("Unsupported policy passage variant")
    if type(policy_tool_assist) is not bool:
        raise ValueError("policy_tool_assist must be bool")
    if type(policy_tool_suite) is not bool:
        raise ValueError("policy_tool_suite must be bool")
    if policy_tool_assist and policy_tool_suite:
        raise ValueError("Select only one experimental policy assist mode")
    _check_pair(case, fixture)
    if policy_tool_assist and (case["request"]["market"] != 1 or
            case["request"]["claim_scope"] != "score_bin_warning_signal_decline_events"):
        raise ValueError("Policy-assisted pilot supports only standard-market full reports")

    # Import after validating context. Do not import the real score DB loader.
    from src import agent

    req = case["request"]
    rows = [
        {"period": period, "score": score, "bin": bin_number,
         "market": req["market"]}
        for period, score, bin_number in zip(fixture["periods"], fixture["scores"], fixture["bins"])
    ]
    docs = policy_passages(fixture["policy_ids"], variant=policy_passage_variant)

    def isolated_history(*, account_id: int) -> list[dict]:
        # Cross-account reads never reach the real database.
        if type(account_id) is not int or account_id != req["account_id"]:
            return []
        return copy.deepcopy(rows)

    def isolated_policies(*, query: str, k: int = 3) -> list[dict]:
        if type(query) is not str or not query.strip():
            raise ValueError("Nonempty policy query required")
        if type(k) is not int or not 1 <= k <= 10:
            raise ValueError("Policy passage limit must be 1..10")
        # Deliberately deterministic: no semantic search or corpus leakage.
        return copy.deepcopy(docs[:k])

    allowed_names = {"get_history", "retrieve_policy"}
    available_schemas = [tool for tool in agent.ALL_TOOLS
                         if type(tool) is dict and tool.get("name") in allowed_names]
    if {item["name"] for item in available_schemas} != allowed_names:
        raise RuntimeError("Agent is missing required benchmark tool schemas")
    restricted_dispatch = {
        "get_history": isolated_history,
        "retrieve_policy": isolated_policies,
    }
    if policy_tool_assist:
        available_schemas.append(copy.deepcopy(ASSIST_TOOL_SCHEMA))
        restricted_dispatch[ASSIST_TOOL_SCHEMA["name"]] = build_assist_dispatch(
            rows, account_id=req["account_id"], as_of_period=req["as_of_period"],
        )
    if policy_tool_suite:
        if req["claim_scope"] not in SUITE_PROMPT_APPENDICES:
            raise ValueError("Unsupported suite claim scope")
        available_schemas.append(copy.deepcopy(SUITE_TOOL_SCHEMA))
        restricted_dispatch[SUITE_TOOL_SCHEMA["name"]] = build_suite_assist_dispatch(
            rows, account_id=req["account_id"], as_of_period=req["as_of_period"],
            market=req["market"],
            site_onboarded_period=req.get("site_onboarded_period"),
            last_exception_review_period=req.get("last_exception_review_period"),
        )
    options = {
        "expected_account_id": req["account_id"],
        "expected_period": req["as_of_period"],
        "expected_market": req["market"],
    }
    if req["claim_scope"] == "grace_decline_events":
        options["site_onboarded_period"] = req["site_onboarded_period"]
        options["last_exception_review_period"] = req["last_exception_review_period"]

    # These patches are process-global; serial benchmark runs ONLY.
    # Every patch restores the original state, even on provider exceptions.
    from contextlib import nullcontext
    if policy_tool_assist:
        prompt_patch = patch.object(
            agent, "CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS",
            agent.CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS + ASSIST_PROMPT_APPENDIX,
        )
    elif policy_tool_suite:
        scope_prompt_attr = {
            "score_bin_warning_signal_decline_events": "CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS",
            "score_bin_warning_signal_no_declines": "CLAIM_FIRST_NO_DECLINES_INSTRUCTIONS",
            "score_bin_warning_signal_quality_hold": "CLAIM_FIRST_QUALITY_HOLD_INSTRUCTIONS",
            "grace_decline_events": "CLAIM_FIRST_GRACE_DECLINES_INSTRUCTIONS",
        }[req["claim_scope"]]
        prompt_patch = patch.object(
            agent, scope_prompt_attr,
            getattr(agent, scope_prompt_attr) + SUITE_PROMPT_APPENDICES[req["claim_scope"]],
        )
    else:
        prompt_patch = nullcontext()
    with patch.dict(agent.ALL_TOOL_DISPATCH, restricted_dispatch, clear=True), \
            patch.object(agent, "ALL_TOOLS", copy.deepcopy(available_schemas)), \
            prompt_patch:
        return agent.run_agent(
            case["question"], client,
            publication_mode=req["publication_mode"],
            claim_scope=req["claim_scope"],
            max_tool_rounds=max_tool_rounds,
            verbose=False,
            **options,
        )


def run_live_benchmark(
    client: Any,
    *,
    provider_id: str,
    max_tool_rounds: int = 4,
    benchmark: dict | None = None,
    fixtures: dict | None = None,
) -> dict:
    """Execute all six pinned cases; return a standard Module 6.2 run report.

    Calling this function IS an authorization to run paid live-model requests;
    the CLI requires a separate explicit confirmation flag.
    """
    _check_client(client)
    _check_round_limit(max_tool_rounds)
    if type(provider_id) is not str or not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("provider_id must be a short safe label")

    def executor(case: dict, fixture: dict) -> dict:
        return run_live_case(case, fixture, client, max_tool_rounds=max_tool_rounds)

    return run_benchmark(
        executor, benchmark=benchmark, fixtures=fixtures,
        provider_id=provider_id, execution_mode="provider_adapter",
    )
