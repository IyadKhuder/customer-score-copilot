"""Module 6.4: one-case, opt-in Gemini/Anthropic policy mismatch diagnostic.

Runs ONLY an isolated synthetic benchmark case. Prints verification comparison
fields without printing/saving the unpublished LLM draft, tool histories, API
credentials, or full account histories. Makes real provider API calls.

Usage:
    python scripts/diagnose_policy_mismatch_64.py --case CF-001 --provider gemini --confirm-live-api
"""
from __future__ import annotations

import argparse


def show_diagnostic(result: dict) -> None:
    """Print claim audit summaries, never the unpublished model answer."""
    print(f"Publication: {'PUBLISH' if result.get('publishable') else 'BLOCK'}")
    print(f"Reason codes: {result.get('reason_codes', [])}")
    print(f"Next action: {result.get('next_action')}")
    print(f"Tool rounds: {result.get('tool_rounds')}")
    audit = result.get('verification')

    rendering = audit.get("rendering", {})

    print("Rendering status:", rendering.get("status"))
    print("Rendering reason:", rendering.get("reason"))
    print("Limitations present:", bool(audit.get("limitations")))
    print("Audit issues present:", bool(audit.get("issues")))

    if not isinstance(audit, dict):
        print('No verification audit attached to the agent result.')
        return
    for layer in ('schema', 'numeric', 'policy'):
        section = audit.get(layer, {})
        print(f"{layer} status: {section.get('status') if isinstance(section, dict) else 'unavailable'}")
    policy = audit.get('policy', {})
    checks = policy.get('checks', []) if isinstance(policy, dict) else []
    if not checks:
        print('No explicit policy claims were audited.')
    for i, entry in enumerate(checks, 1):
        print(f"\nPolicy claim {i}: {entry.get('status')}")
        detail = entry.get('audit', {})
        if not isinstance(detail, dict):
            print('  No structured detail was returned.')
            continue
        if isinstance(detail.get('reason'), str):
            print(f"  Reason: {detail['reason']}")
        if isinstance(detail.get('error'), str):
            print(f"  Validator error class omitted; local inspection needed.")
        for check in detail.get('checks', []):
            print(f"  Field:           {check.get('field')}")
            print(f"  Gemini claimed:  {check.get('claimed')!r}")
            print(f"  Python expected: {check.get('expected')!r}")
            print(f"  Context matches: {check.get('context_matches')}")
            print(f"  Value matches:   {check.get('value_matches')}")
        for unchecked in detail.get('unchecked_claims', []):
            print(f"  Unchecked claim reason: {unchecked.get('reason')}")


def main() -> None:
    parser = argparse.ArgumentParser(description='Diagnose one isolated live benchmark policy mismatch')
    parser.add_argument('--case', choices=[f'CF-{i:03d}' for i in range(1, 7)], default='CF-001')
    parser.add_argument('--provider', choices=('gemini', 'anthropic'), default='gemini')
    parser.add_argument('--confirm-live-api', action='store_true', help='Authorize a real provider request')
    args = parser.parse_args()
    if not args.confirm_live_api:
        parser.error('Real provider calls require --confirm-live-api')

    from src.evaluation_dataset import load_benchmark
    from src.evaluation_runner import load_fixtures
    from src.live_evaluation import run_live_case
    from src.llm_client import get_llm_client

    benchmark = load_benchmark()
    fixtures = load_fixtures(benchmark=benchmark)
    case = next(c for c in benchmark['cases'] if c['id'] == args.case)
    fixture = next(f for f in fixtures['fixtures'] if f['scenario_id'] == case['scenario_id'])
    print(f"Running {args.case} using isolated synthetic fixtures and {args.provider}...")
    result = run_live_case(case, fixture, get_llm_client(args.provider))
    show_diagnostic(result)
    print('\nDiagnostic only. No raw model replies or sensitive evidence saved.')


if __name__ == '__main__':
    main()
