"""Diagnose ONE suite-assisted benchmark case without publishing LLM drafts.

Run from the project root:
  python -m scripts.diagnose_assisted_case_645 --case CF-003 \
      --provider gemini --policy-passages clarified_v1 --confirm-live-api

Uses only the isolated synthetic scenario and trusted policy-assist tool.
Calls a real provider ONLY with explicit --confirm-live-api. Does not persist
provider outputs, tool evidence, raw histories, credentials, or LLM drafts.
"""
from __future__ import annotations

import argparse
from typing import Any, Callable


def safe_summary(result: dict) -> list[str]:
    """Show an audit summary without printing LLM-generated prose or history."""
    if not isinstance(result, dict):
        raise TypeError("Agent result must be a dictionary")
    lines = [
        f"Publication: {'PUBLISH' if result.get('publishable') is True else 'BLOCK'}",
        f"Reason codes: {result.get('reason_codes', [])}",
        f"Next action: {result.get('next_action')}",
        f"Tool rounds: {result.get('tool_rounds')}",
    ]
    tools = result.get('tool_evidence', [])
    called = {entry.get('tool_name') for entry in tools if isinstance(entry, dict)} if isinstance(tools, list) else set()
    lines.extend([
        f"History called: {'get_history' in called}",
        f"Policy retrieved: {'retrieve_policy' in called}",
        f"Policy helper called: {'evaluate_policy_signals' in called}",
    ])
    audit = result.get('verification')
    if not isinstance(audit, dict):
        return lines + ["No internal verification audit available for this execution."]
    lines.append(f"Overall audit status: {audit.get('status')}")
    for part in ('schema', 'numeric', 'policy', 'rendering'):
        section = audit.get(part)
        if isinstance(section, dict):
            lines.append(f"{part.capitalize()} status: {section.get('status')}")
    lines.extend([
        f"Limitations present: {bool(audit.get('limitations'))}",
        f"Audit issues present: {bool(audit.get('issues'))}",
    ])
    rendering = audit.get('rendering')
    if isinstance(rendering, dict) and isinstance(rendering.get('reason'), str):
        # The renderer's exception text is application-authored, not a raw
        # model error, provider response, or user-provided tool payload.
        lines.append(f"Composer reason: {rendering['reason']}")
    policy = audit.get('policy')
    if isinstance(policy, dict) and isinstance(policy.get('checks'), list):
        for check in policy['checks']:
            if not isinstance(check, dict):
                continue
            claim = check.get('claim')
            if not isinstance(claim, dict):
                continue
            field = claim.get('field')
            if field not in ('warning_signal_status', 'raw_event_count.consecutive_declines'):
                continue
            lines.append(f"Policy check {field}: {check.get('status')}")
            detail = check.get('audit')
            if isinstance(detail, dict) and isinstance(detail.get('checks'), list):
                for item in detail['checks']:
                    if isinstance(item, dict) and item.get('field') == field:
                        lines.append(f"  Value agrees: {item.get('value_matches') is True}")
                        lines.append(f"  Context agrees: {item.get('context_matches') is True}")
    return lines


def main(
    argv: list[str] | None = None, *,
    client_factory: Callable[[str], Any] | None = None,
    case_runner: Callable[..., dict] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Diagnose one policy-assisted quality-hold scenario")
    parser.add_argument('--case', choices=('CF-003', 'CF-004', 'CF-006'), required=True)
    parser.add_argument('--provider', choices=('gemini', 'anthropic'), default='gemini')
    parser.add_argument('--policy-passages', choices=('baseline', 'clarified_v1'), default='clarified_v1')
    parser.add_argument('--confirm-live-api', action='store_true')
    args = parser.parse_args(argv)
    if not args.confirm_live_api:
        parser.error("Live requests require --confirm-live-api")

    from src.evaluation_dataset import load_benchmark
    from src.evaluation_runner import load_fixtures
    if client_factory is None:
        from src.llm_client import get_llm_client
        client_factory = get_llm_client
    if case_runner is None:
        from src.live_evaluation import run_live_case
        case_runner = run_live_case

    benchmark = load_benchmark()
    cases = {item['id']: item for item in benchmark['cases']}
    case = cases[args.case]
    fixtures = load_fixtures(benchmark=benchmark)
    by_scenario = {item['scenario_id']: item for item in fixtures['fixtures']}
    print(f"Case: {args.case}; provider: {args.provider}; policy passages: {args.policy_passages}")
    try:
        result = case_runner(
            case, by_scenario[case['scenario_id']], client_factory(args.provider),
            policy_passage_variant=args.policy_passages,
            policy_tool_suite=True,
        )
    except Exception as exc:
        print(f"Execution error type: {type(exc).__name__}")
        print("No raw provider errors or private evidence displayed.")
        return 2
    for line in safe_summary(result):
        print(line)
    print("Diagnostic only; no provider drafts, histories, or credentials saved.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
