"""Opt-in ONE-CASE diagnostic of an experimental deterministic policy helper.

No raw model text, account history, policy text or credentials are printed or saved.
This is an isolated fixture run; it is NOT a normal production agent mode.
"""
from __future__ import annotations

import argparse

from scripts.diagnose_policy_evidence_643 import summarize


def main(argv: list[str] | None = None, *, client_factory=None, case_runner=None) -> int:
    parser = argparse.ArgumentParser(description="CF-001 deterministic policy helper experiment")
    parser.add_argument("--provider", choices=("gemini", "anthropic"), default="gemini")
    parser.add_argument("--policy-passages", choices=("baseline", "clarified_v1"),
                        default="clarified_v1")
    parser.add_argument("--confirm-live-api", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_live_api:
        parser.error("Real provider requests require --confirm-live-api")

    from src.evaluation_dataset import load_benchmark
    from src.evaluation_runner import load_fixtures
    from src.live_evaluation import run_live_case
    if client_factory is None:
        from src.llm_client import get_llm_client
        client_factory = get_llm_client
    if case_runner is None:
        case_runner = run_live_case

    benchmark = load_benchmark()
    case = next(c for c in benchmark["cases"] if c["id"] == "CF-001")
    fixtures = load_fixtures(benchmark=benchmark)
    fixture = next(f for f in fixtures["fixtures"] if f["scenario_id"] == case["scenario_id"])
    print("CF-001 experimental mode: deterministic policy tool ASSISTED")
    print(f"Provider: {args.provider}; policy passages: {args.policy_passages}")
    result = case_runner(case, fixture, client_factory(args.provider),
                         policy_passage_variant=args.policy_passages,
                         policy_tool_assist=True)
    evidence = result.get("tool_evidence", [])
    called = [item.get("tool_name") for item in evidence if isinstance(item, dict)]
    print("Policy helper called:", "evaluate_policy_signals" in called)
    print("History called:", "get_history" in called)
    print("Policy retrieved:", "retrieve_policy" in called)
    for line in summarize(result):
        print(line)
    print("No provider drafts, score histories, policy text, or credentials saved.")
    print("This tool-assisted mode measures USE OF trusted evaluations, not independent LLM calculations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
