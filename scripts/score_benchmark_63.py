"""Module 6.3: score a saved Module 6.2 JSON report without API calls.

Example, from repository root:
  python -m scripts.score_benchmark_63 --input results/module_6_2_scripted.json \
      --output results/module_6_3_scripted_scores.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.evaluation_metrics import score_report
from src.evaluation_runner import save_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a Module 6.2 run report")
    parser.add_argument("--input", required=True, type=Path,
                        help="JSON run report saved by Module 6.2")
    parser.add_argument("--output", required=True, type=Path,
                        help="New scored JSON report (will not overwrite)")
    args = parser.parse_args()
    with args.input.open("r", encoding="utf-8") as stream:
        report = json.load(stream)
    result = score_report(report)
    destination = save_report(result, args.output)
    metrics = result["metrics"]
    print(f"Scored {metrics['cases_total']} benchmark cases")
    for row in result["cases"]:
        print(f"  {row['case_id']} ({row['test_type']}): "
              f"{'PASS' if row['passed'] else 'FAIL'}")
    print(f"Publication decision accuracy: {metrics['publication_decision_accuracy']:.1%}")
    print(f"Required-policy ID coverage: {metrics['required_policy_retrieval_coverage']:.1%}")
    print(f"Expected blocks correctly blocked: {metrics['correct_blocks']}/"
          f"{metrics['expected_block_cases']}")
    print(f"Report: {destination}")
    if result["execution_mode"] == "scripted_offline":
        print("Note: scripted fixture replay; NOT measured real-provider quality.")
    return 0 if metrics["cases_passed_all_checks"] == metrics["cases_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
