"""Opt-in live notes for an existing scan; never rewrite the input report."""
import argparse
import json
from pathlib import Path

from src.batch_scan import save_scan_report


def main():
    parser = argparse.ArgumentParser(description='Generate bounded verified notes from an existing offline scan.')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', choices=['gemini','anthropic'], required=True)
    parser.add_argument('--daily-remaining', type=int, required=True, help='Caller-confirmed remaining allowance, 0..500')
    parser.add_argument('--max-requests', type=int, default=8)
    parser.add_argument('--max-notes', type=int, choices=range(1,6), default=2)
    parser.add_argument('--interval-seconds', type=float, default=5.0)
    parser.add_argument('--max-tool-rounds', type=int, choices=range(1,9), default=4)
    parser.add_argument('--confirm-live', action='store_true', help='Explicitly allow provider requests; confirm actual provider quotas first')
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error('Live execution requires --confirm-live. Offline scans use scripts.scan_batch_712.')
    log = args.output.with_suffix('.requests.jsonl')
    if args.output.exists() or log.exists():
        parser.error('Output or request log exists; select a new filename')
    from src.request_budget import RequestBudget
    from src.batch_notes import add_verified_notes, _validate_scan
    from src.batch_scan import ROOT
    try:
        budget = RequestBudget(daily_remaining=args.daily_remaining, max_requests=args.max_requests,
                               interval_seconds=args.interval_seconds)
        report = json.loads(args.input.read_text(encoding='utf-8'))
        _validate_scan(report, ROOT)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'Invalid scan or budget ({type(exc).__name__}); no provider request started.\n')
    from src.config import get_provider_config
    from src.llm_client import get_llm_client
    client = None
    try:
        config = get_provider_config(args.provider)
        client = get_llm_client(args.provider)
        budget.attach_log(log)
        enriched = add_verified_notes(report, client, budget, adapter_kind=config['adapter'],
                                     max_notes=args.max_notes, max_tool_rounds=args.max_tool_rounds)
        # Safe configuration fields only; do not serialize config containing api_key.
        enriched['notes']['provider'] = args.provider
        enriched['notes']['configured_model'] = config['model']
        enriched['notes']['model_identity_verified'] = False
        enriched['notes']['served_model'] = None
        save_scan_report(enriched, args.output)
        print(json.dumps(enriched['notes'], indent=2))
        return 1 if enriched['notes']['outcomes']['failed'] else 0
    except Exception as exc:
        parser.exit(2, f'Note run could not be saved ({type(exc).__name__}); input scan unchanged.\n')
    finally:
        budget.close()
        if client is not None:
            client.client.close()


if __name__ == '__main__':
    raise SystemExit(main())
