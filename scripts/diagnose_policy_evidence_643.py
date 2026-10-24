"""Single-case live policy-passage A/B diagnostic. Safe console-only summary.

Example:
    python -m scripts.diagnose_policy_evidence_643 --case CF-001 \
      --provider gemini --policy-passages clarified_v1 --confirm-live-api

Uses isolated synthetic fixtures; never prints model drafts, history, policy
source text, keys, or exception messages. Real API requests may cost money.
"""
from __future__ import annotations

import argparse

from src.policy_passage_variants import POLICY_PASSAGE_VARIANTS


def summarize(result: dict) -> list[str]:
    lines = [
        f"Publication: {'PUBLISH' if result.get('publishable') else 'BLOCK'}",
        f"Reason codes: {result.get('reason_codes', [])}",
        f"Next action: {result.get('next_action')}",
        f"Tool rounds: {result.get('tool_rounds')}",
    ]
    audit = result.get('verification')
    if not isinstance(audit, dict):
        return lines + ['No verification audit available.']
    for layer in ('schema', 'numeric', 'policy', 'rendering'):
        section = audit.get(layer)
        if isinstance(section, dict):
            lines.append(f"{layer} status: {section.get('status')}")
    lines.append(f"Limitations present: {bool(audit.get('limitations'))}")
    policy = audit.get('policy', {})
    if isinstance(policy, dict):
        for check in policy.get('checks', []):
            field = check.get('claim', {}).get('field')
            status = check.get('status')
            lines.append(f"Policy field: {field} [{status}]")
            detail = check.get('audit', {})
            if isinstance(detail, dict):
                for item in detail.get('checks', []):
                    if item.get('field') == field:
                        lines.append(f"  Claimed: {item.get('claimed')!r}; expected: {item.get('expected')!r}")
    return lines


def main(argv: list[str] | None = None, *, client_factory=None, case_runner=None) -> int:
    parser = argparse.ArgumentParser(description='Diagnose one isolated live scenario with versioned policy text')
    parser.add_argument('--case', choices=[f'CF-{i:03d}' for i in range(1, 7)], default='CF-001')
    parser.add_argument('--provider', choices=('gemini', 'anthropic'), default='gemini')
    parser.add_argument('--policy-passages', choices=POLICY_PASSAGE_VARIANTS, required=True)
    parser.add_argument('--confirm-live-api', action='store_true')
    args = parser.parse_args(argv)
    if not args.confirm_live_api:
        parser.error('Real provider requests require --confirm-live-api')

    from src.evaluation_dataset import load_benchmark
    from src.evaluation_runner import load_fixtures
    from src.live_evaluation import run_live_case
    if client_factory is None:
        from src.llm_client import get_llm_client
        client_factory = get_llm_client
    if case_runner is None:
        case_runner = run_live_case
    benchmark = load_benchmark()
    fixtures = load_fixtures(benchmark=benchmark)
    case = next(c for c in benchmark['cases'] if c['id'] == args.case)
    fixture = next(f for f in fixtures['fixtures'] if f['scenario_id'] == case['scenario_id'])
    print(f"Case: {args.case}; provider: {args.provider}; evidence variant: {args.policy_passages}")
    result = case_runner(
        case, fixture, client_factory(args.provider),
        policy_passage_variant=args.policy_passages,
    )
    for line in summarize(result):
        print(line)
    print('Diagnostic only; provider output and raw evidence not saved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
