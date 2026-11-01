"""Run M1.3's scripted role comparison without provider calls."""
import argparse
import json
from pathlib import Path

from src.role_comparison import DEFAULT_FIXTURES, compare_scripted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True, help='New local JSON report path')
    args = parser.parse_args()
    report = compare_scripted(args.fixtures, repetitions=args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    for mode, summary in report['summary'].items():
        print(f"{mode}: {summary['expected_outcome_matches']}/{summary['cases']} scripted outcomes matched; "
              f"{summary['incorrect_publications']} incorrect publications; "
              f"{summary['unnecessary_holds']} unnecessary holds")
    print(f'Report: {args.output}')
    print('Scripted offline comparison only; no LLM capability or live latency/cost measured.')


if __name__ == '__main__':
    main()
