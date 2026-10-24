"""Module 6.4: opt-in, six-case live provider benchmark.

Example from the project root (network and possible usage fees):
    python -m scripts.run_live_benchmark_64 --provider gemini \
        --model-label YOUR_CONFIGURED_MODEL --output results/gemini_run_01.json \
        --confirm-live-api

Use an isolated Python process; this benchmark temporarily patches agent tools.
No API key, model reply, prompt, or raw tool history is saved to the JSON report.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.evaluation_runner import save_report
from src.live_evaluation import run_live_benchmark

_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}\Z")


def main(argv: list[str] | None = None, *, client_factory=None) -> int:
    parser = argparse.ArgumentParser(description="Run six claim-first benchmark cases via a live LLM")
    parser.add_argument("--provider", choices=("gemini", "anthropic"), required=True)
    parser.add_argument("--model-label", required=True,
                        help="Nonsecret model identifier as configured in src/config.py")
    parser.add_argument("--output", type=Path, required=True,
                        help="New run-report JSON (existing files are never overwritten)")
    parser.add_argument("--max-tool-rounds", type=int, default=4)
    parser.add_argument("--confirm-live-api", action="store_true",
                        help="Explicitly permit network calls and any associated API charges")
    args = parser.parse_args(argv)
    if not _SAFE_LABEL.fullmatch(args.model_label):
        parser.error("--model-label must use 1–50 safe nonsecret characters")
    if not 1 <= args.max_tool_rounds <= 8:
        parser.error("--max-tool-rounds must be from 1 to 8")
    if not args.confirm_live_api:
        parser.error("Live API calls require --confirm-live-api; no requests were made")
    if args.output.exists():
        parser.error("--output already exists; choose a new filename")
    if client_factory is None:
        # The app's real adapter is imported ONLY after opt-in.
        from src.llm_client import get_llm_client
        client_factory = get_llm_client
    client = client_factory(args.provider)
    report = run_live_benchmark(
        client,
        provider_id=f"{args.provider}.{args.model_label}",
        max_tool_rounds=args.max_tool_rounds,
    )
    destination = save_report(report, args.output)
    print(f"Live-provider benchmark: {len(report['cases'])} cases")
    for row in report["cases"]:
        observed = row["observed"]
        state = ("ERROR " + row["error_type"]) if observed is None else (
            "PUBLISH" if observed["publishable"] else "BLOCK"
        )
        print(f"  {row['case_id']}: {state} ({row['elapsed_ms']} ms)")
    print(f"Report: {destination}")
    print("Note: controlled synthetic fixtures; score separately with Module 6.3.")
    return 1 if any(row["error_type"] is not None for row in report["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
