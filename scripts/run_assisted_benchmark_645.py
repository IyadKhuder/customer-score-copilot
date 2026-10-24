"""Module 6.4.5 — opt-in six-case policy-tool-assisted LLM benchmark.

From repository root (may consume provider quota):
    python -m scripts.run_assisted_benchmark_645 --provider gemini \
      --model-label gemini-3.5-flash-lite --policy-passages clarified_v1 \
      --output results/module_6_4_5_gemini_assisted_01.json --confirm-live-api

The output is the UNCHANGED Module 6.2 observation schema and can be scored
using scripts.score_benchmark_63. No raw provider drafts, history or keys saved.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from src.evaluation_runner import run_benchmark, save_report
from src.live_evaluation import run_live_case
from src.policy_passage_variants import POLICY_PASSAGE_VARIANTS

_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}\Z")


def run_assisted_benchmark(
    client: Any, *, provider_id: str, policy_passage_variant: str = "clarified_v1",
    max_tool_rounds: int = 4, benchmark: dict | None = None,
    fixtures: dict | None = None,
) -> dict:
    """Execute six cases with read-only fixture-bound policy assistance.

    The benchmark case/fixture pair is validated by the Module 6.2 runner.
    Execution is serial because agent tool registration is temporarily patched.
    """
    if not (callable(getattr(client, "chat", None)) and
            callable(getattr(client, "continue_with_tool_results", None))):
        raise TypeError("A live provider client is required")
    if type(provider_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", provider_id):
        raise ValueError("Invalid provider identifier")
    if policy_passage_variant not in POLICY_PASSAGE_VARIANTS:
        raise ValueError("Unsupported policy variant")
    if type(max_tool_rounds) is not int or not 1 <= max_tool_rounds <= 8:
        raise ValueError("max_tool_rounds must be between 1 and 8")

    def executor(case: dict, fixture: dict) -> dict:
        return run_live_case(
            case, fixture, client, max_tool_rounds=max_tool_rounds,
            policy_passage_variant=policy_passage_variant,
            policy_tool_suite=True,
        )

    return run_benchmark(
        executor, benchmark=benchmark, fixtures=fixtures,
        provider_id=provider_id, execution_mode="provider_adapter",
    )


def main(argv: list[str] | None = None, *, client_factory=None,
         benchmark_runner=None) -> int:
    parser = argparse.ArgumentParser(description="Run six isolated policy-assisted benchmark cases")
    parser.add_argument("--provider", required=True, choices=("gemini", "anthropic"))
    parser.add_argument("--model-label", required=True, help="Configured provider model ID (label only)")
    parser.add_argument("--policy-passages", choices=POLICY_PASSAGE_VARIANTS,
                        default="clarified_v1")
    parser.add_argument("--max-tool-rounds", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True,
                        help="New JSON result report, never overwritten")
    parser.add_argument("--confirm-live-api", action="store_true")
    args = parser.parse_args(argv)
    if not _SAFE_LABEL.fullmatch(args.model_label):
        parser.error("Model label must be 1–50 nonsecret safe characters")
    if not 1 <= args.max_tool_rounds <= 8:
        parser.error("max-tool-rounds must be between 1 and 8")
    if not args.confirm_live_api:
        parser.error("Live requests require --confirm-live-api")
    if args.output.exists():
        parser.error("Output already exists; choose a different filename")

    if client_factory is None:
        from src.llm_client import get_llm_client
        client_factory = get_llm_client
    client = client_factory(args.provider)
    run = run_assisted_benchmark if benchmark_runner is None else benchmark_runner
    condition = f"{args.provider}.{args.model_label}.assisted_v1"
    report = run(
        client, provider_id=condition,
        policy_passage_variant=args.policy_passages,
        max_tool_rounds=args.max_tool_rounds,
    )
    destination = save_report(report, args.output)
    print(f"Policy-tool-assisted benchmark: {len(report['cases'])} cases")
    print(f"Provider: {args.provider}; passage variant: {args.policy_passages}")
    for row in report["cases"]:
        observed = row["observed"]
        outcome = ("ERROR " + row["error_type"]) if observed is None else (
            "PUBLISH" if observed["publishable"] else "BLOCK")
        called = observed is not None and "evaluate_policy_signals" in observed["tool_names"]
        print(f"  {row['case_id']}: {outcome} | helper called: {called} "
              f"| {row['elapsed_ms']} ms")
    print(f"Report: {destination}")
    print("Controlled synthetic histories; real LLM tool-use, not independent policy arithmetic.")
    print("Score with scripts.score_benchmark_63; no raw LLM drafts or credentials saved.")
    return 1 if any(row["error_type"] for row in report["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
