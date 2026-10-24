"""Capture an OFFLINE PLAN, not evidence that a provider run occurred."""
import argparse
from pathlib import Path
from src.experiment_metadata import build_experiment_metadata, save_experiment_metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path('.'))
    parser.add_argument('--provider', choices=('gemini', 'anthropic'), required=True)
    parser.add_argument('--policy-passages', choices=('baseline', 'clarified_v1'), default='clarified_v1')
    parser.add_argument('--max-tool-rounds', type=int, default=4)
    parser.add_argument('--between-case-seconds', type=float, default=65)
    parser.add_argument('--retry-delay-seconds', type=float, default=65)
    parser.add_argument('--max-rate-limit-retries', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Output exists; choose a new metadata filename')
    try:
        metadata = build_experiment_metadata(
            args.project_root, provider=args.provider, policy_passages=args.policy_passages,
            max_tool_rounds=args.max_tool_rounds, between_case_seconds=args.between_case_seconds,
            retry_delay_seconds=args.retry_delay_seconds, max_rate_limit_retries=args.max_rate_limit_retries)
        destination = save_experiment_metadata(metadata, args.output)
    except (ValueError, OSError, SyntaxError) as exc:
        # Do not echo exception contents: parser errors could contain source content.
        parser.error(f'Metadata capture failed ({type(exc).__name__}); check inputs and destination')
    print(f'Offline preflight plan saved: {destination}')
    print('No provider calls. This file does not certify an executed experiment.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
