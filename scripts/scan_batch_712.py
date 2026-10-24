"""Run from the project root: python -m scripts.scan_batch_712 --help."""
import argparse
import json
from pathlib import Path

from src.batch_scan import InvalidScanRequest, scan_batch, save_scan_report


def main():
    parser = argparse.ArgumentParser(description='Offline market-1 anomaly scan; no API calls.')
    parser.add_argument('--accounts', nargs='+', type=int, required=True)
    parser.add_argument('--as-of', required=True, help='Reporting month YYYY-MM-01')
    parser.add_argument('--db', type=Path, default=Path('data/scores.db'))
    parser.add_argument('--output', type=Path, required=True, help='New JSON path in an existing directory')
    args = parser.parse_args()
    # Refuse an existing output before doing any work; exclusive write also handles races.
    if args.output.exists():
        parser.error('Output already exists; choose a new filename.')
    try:
        report = scan_batch(dict(schema_version=1, account_ids=args.accounts,
                                 market=1, as_of_period=args.as_of), args.db)
        save_scan_report(report, args.output)
    except (InvalidScanRequest, OSError) as exc:
        parser.exit(2, f'Scan not saved ({type(exc).__name__}); check arguments and paths.\n')
    print(json.dumps(dict(run_status=report['run_status'], summary=report['summary']), indent=2))
    return 1 if report['run_status'] != 'completed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
