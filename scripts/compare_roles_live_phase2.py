"""Credential-free plan by default; explicit live opt-in for Gemini only."""
import argparse
import json
from pathlib import Path

from src.role_comparison_live import make_plan, run_live


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['smoke', 'pilot'], default='smoke')
    parser.add_argument('--confirm-live-api', action='store_true')
    parser.add_argument('--retain-rejected-output', action='store_true',
                        help='Opt-in local unverified text capture, at most 4096 UTF-8 bytes per rejected response')
    parser.add_argument('--daily-remaining', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        plan = make_plan(suite=args.suite, retain_rejected_output=args.retain_rejected_output)
        if not args.confirm_live_api:
            print(json.dumps(dict(execution_mode='dry_run_no_provider_requests', plan=plan), indent=2))
            return 0
        if args.daily_remaining is None or args.output is None:
            parser.error('Live execution requires --daily-remaining and a NEW --output directory')
        report = run_live(plan, args.output, daily_remaining=args.daily_remaining, confirm_live_api=True)
        print(json.dumps(dict(status=report['status'], budget=report['budget'],
                              report=str(args.output / 'report.json')), indent=2))
        return 0 if report['status'] == 'completed' else 2
    except (ValueError, OSError):
        print('Pilot configuration/output error. Check plan, request allowance and new output path.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
