"""Opt-in metadata-bound provider experiment. No implicit live API calls."""
import argparse
import json
from pathlib import Path
from src.provider_experiment import run_provider_experiment
from src.request_budget import RequestBudget


def main(argv=None, *, client_factory=None, sleeper=None, clock=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--confirm-live-api',action='store_true')
    p.add_argument('--daily-requests-remaining', type=int, required=True, help='Conservative allowance confirmed by caller; account-wide usage is not queried')
    p.add_argument('--max-api-requests', type=int, default=24)
    a=p.parse_args(argv)
    if not a.confirm_live_api: p.error('Provider execution requires --confirm-live-api')
    if a.output_dir.exists(): p.error('Output directory exists; choose a new one')
    root=Path(__file__).resolve().parent.parent
    try:
        plan=json.loads(a.plan.read_text(encoding='utf-8'))
        budget_options = {}
        if sleeper is not None: budget_options['sleeper'] = sleeper
        if clock is not None: budget_options['clock'] = clock
        budget = RequestBudget(daily_remaining=a.daily_requests_remaining, max_requests=a.max_api_requests, **budget_options)
        report,scores,meta=run_provider_experiment(plan,root,a.output_dir,confirm_live_api=True,
                                                 client_factory=client_factory,sleeper=sleeper,clock=clock,request_budget=budget)
    except Exception as exc:
        # Avoid exposing SDK messages, credentials or unpublished drafts.
        print(f'Experiment failed: {type(exc).__name__}. Any reserved output directory is incomplete; preserve it.')
        return 2
    m=scores['metrics']
    print(f"Saved {len(report['cases'])} cases; all-checks PASS {m['cases_passed_all_checks']}/{m['cases_total']}; execution errors {m['execution_error_count']}")
    print(f'API requests started: {budget.started}/{budget.limit}; SDK retries disabled; request log saved')
    stable=meta['execution_record']['source_snapshot_unchanged_after_run']
    print(f'Source snapshot unchanged: {stable}; bundle: {a.output_dir}')
    return 0 if m['cases_passed_all_checks']==m['cases_total'] and stable else 1


if __name__=='__main__': raise SystemExit(main())
