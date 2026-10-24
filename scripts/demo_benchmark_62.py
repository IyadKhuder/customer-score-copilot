"""Module 6.2: offline scripted benchmark replay; does not contact an LLM.

From project root:
    python -m scripts.demo_benchmark_62 --output results/module_6_2_scripted.json

Output is local and intentionally not committed by default. Scripted answers
are pinned test transcripts and do NOT demonstrate model accuracy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.evaluation_runner import run_benchmark, run_scripted_case, save_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run six synthetic offline benchmark scenarios")
    parser.add_argument(
        "--output", type=Path, default=Path("results/module_6_2_scripted.json"),
        help="New JSON report filename (existing reports will not be overwritten)",
    )
    args = parser.parse_args()
    report = run_benchmark(run_scripted_case, provider_id="scripted_offline")
    output = save_report(report, args.output)
    print(f"Offline scripted execution: {len(report['cases'])} cases")
    for record in report["cases"]:
        observed = record["observed"]
        state = ("ERROR " + record["error_type"]) if observed is None else (
            "PUBLISH" if observed["publishable"] else "BLOCK"
        )
        print(f"  {record['case_id']} ({record['scenario_id']}): {state}")
    print(f"Report: {output}")
    print("Note: scripted fixture replay, NOT real LLM/provider evaluation.")
    return 1 if any(row["error_type"] is not None for row in report["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
