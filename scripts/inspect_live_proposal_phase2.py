"""Read a saved paired case with current metrics. No writes or provider calls."""
import argparse
import json
from pathlib import Path

from src.review_workflow import digest
from src.role_comparison import validate_proposal
from src.role_comparison_live import assets, reference_assessment


def inspect_saved_case(directory, case_id):
    directory = Path(directory)
    fixtures, _ = assets()
    fixture = next((c for c in fixtures['paired'] if c['case_id'] == case_id), None)
    if fixture is None:
        raise ValueError('Unknown paired fixture')
    report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    if report['plan']['fixture_hash'] != digest(fixtures):
        raise ValueError('Saved report uses different fixtures')
    saved = json.loads((directory / (case_id + '.proposal.json')).read_text(encoding='utf-8'))
    proposal = saved['proposal']
    if proposal is not None:
        proposal = validate_proposal(proposal)
    case = next(c for c in report['paired'] if c['case_id'] == case_id)
    for mode in ('analysis_only', 'analysis_plus_review'):
        if (case[mode]['snapshot_hash'] != digest(fixture['evidence'])
                or case[mode]['proposal_hash'] != (digest(proposal) if proposal is not None else None)):
            raise ValueError('Saved evidence/proposal binding mismatch')
    return dict(case_id=case_id, saved_bindings_verified=True,
                original_reference_proposal_correct=case['analysis_only']['reference_proposal_correct'],
                current_reference_assessment=reference_assessment(fixture['evidence'], proposal),
                original_report_modified=False, provider_requests=0,
                scope='Current structured assessment of saved data; not a new model run or recovery of rejected output.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--case-id', default='LP-A2')
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_saved_case(args.input, args.case_id), indent=2))
        return 0
    except (ValueError, OSError, KeyError, StopIteration):
        print('Saved bundle is missing, incompatible or has mismatched evidence/proposal hashes.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
