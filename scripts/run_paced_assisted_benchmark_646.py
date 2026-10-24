"""Opt-in, paced six-case provider benchmark; never overwrite prior results.

Example (real calls / quota consumption):
python -m scripts.run_paced_assisted_benchmark_646 --provider gemini \
  --model-label gemini-3.5-flash-lite --policy-passages clarified_v1 \
  --between-case-seconds 65 --retry-delay-seconds 65 \
  --max-rate-limit-retries 1 \
  --output results/module_6_4_6_gemini_assisted_02.json --confirm-live-api
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.evaluation_runner import save_report
from src.paced_assisted_benchmark import run_paced_assisted_benchmark
from src.policy_passage_variants import POLICY_PASSAGE_VARIANTS

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}\Z")


def main(argv: list[str] | None = None, *, client_factory=None,
         benchmark_runner=None) -> int:
    parser = argparse.ArgumentParser(description="Run paced, rate-limit-aware assisted benchmark")
    parser.add_argument("--provider", required=True, choices=("gemini", "anthropic"))
    parser.add_argument("--model-label", required=True, help="Metadata label; config.py selects model")
    parser.add_argument("--policy-passages", choices=POLICY_PASSAGE_VARIANTS,
                        default="clarified_v1")
    parser.add_argument("--max-tool-rounds", type=int, default=4)
    parser.add_argument("--between-case-seconds", type=float, default=65.0)
    parser.add_argument("--retry-delay-seconds", type=float, default=65.0)
    parser.add_argument("--max-rate-limit-retries", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-live-api", action="store_true")
    args = parser.parse_args(argv)

    if not _LABEL.fullmatch(args.model_label):
        parser.error("Invalid model label")
    if not args.confirm_live_api:
        parser.error("Live provider calls require --confirm-live-api")
    if args.output.exists():
        parser.error("Output exists; use a new report filename")
    if not 1 <= args.max_tool_rounds <= 8:
        parser.error("max-tool-rounds must be between 1 and 8")
    if not 0 <= args.between_case_seconds <= 600:
        parser.error("between-case-seconds must be between 0 and 600")
    if not 0 <= args.retry_delay_seconds <= 600:
        parser.error("retry-delay-seconds must be between 0 and 600")
    if not 0 <= args.max_rate_limit_retries <= 3:
        parser.error("max-rate-limit-retries must be between 0 and 3")

    # Keep this label distinct from the original six-case run; its model label
    # remains metadata and does NOT replace src/config.py's configured model.
    condition = f"{args.provider}.{args.model_label}.assisted_paced_v1"
    if len(condition) > 64:
        parser.error("Provider/model label combined exceeds 64 characters")
    if client_factory is None:
        from src.llm_client import get_llm_client
        client_factory = get_llm_client
    client = client_factory(args.provider)
    runner = run_paced_assisted_benchmark if benchmark_runner is None else benchmark_runner
    report = runner(
        client, provider_id=condition,
        policy_passage_variant=args.policy_passages,
        max_tool_rounds=args.max_tool_rounds,
        between_case_seconds=args.between_case_seconds,
        retry_delay_seconds=args.retry_delay_seconds,
        max_rate_limit_retries=args.max_rate_limit_retries,
    )
    destination = save_report(report, args.output)
    print(f"Paced policy-assisted benchmark: {len(report['cases'])} cases")
    print(f"Provider: {args.provider}; policy passages: {args.policy_passages}")
    print(f"Between-case pause: {args.between_case_seconds:g}s; "
          f"429 retries per case: {args.max_rate_limit_retries}")
    for row in report["cases"]:
        observed = row["observed"]
        outcome = "ERROR " + row["error_type"] if observed is None else (
            "PUBLISH" if observed["publishable"] else "BLOCK")
        called = bool(observed and "evaluate_policy_signals" in observed["tool_names"])
        print(f"  {row['case_id']}: {outcome} | helper called: {called} "
              f"| {row['elapsed_ms']} ms")
    print(f"Report: {destination}")
    print("Paced evaluation with synthetic fixtures; not independent LLM policy arithmetic.")
    print("Never overwrites earlier reports; score separately with Module 6.3.")
    return 1 if any(row["error_type"] for row in report["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
