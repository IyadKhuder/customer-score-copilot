"""Offline orchestration checks, not tests of model intelligence."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from src.role_comparison import (compare_scripted, load_cases, run_roles,
                                 validate_evidence, validate_findings)


def first_case():
    return deepcopy(load_cases()['cases'][0])


def roles(case):
    return lambda evidence: deepcopy(case['proposal']), lambda evidence, proposal: deepcopy(case['reviewer_response'])


def test_paired_fixture_comparison_reports_failures_and_false_holds_honestly():
    report = compare_scripted(repetitions=2)
    baseline = report['summary']['analysis_only']
    reviewed = report['summary']['analysis_plus_review']
    assert baseline['cases'] == reviewed['cases'] == 10
    assert baseline['expected_outcome_matches'] == 10
    assert reviewed['expected_outcome_matches'] == 7
    assert baseline['incorrect_publications'] == reviewed['incorrect_publications'] == 0
    assert baseline['unnecessary_holds'] == 0
    assert reviewed['unnecessary_holds'] == 3
    assert baseline['role_invocations_per_repetition'] == {'analysis': 10, 'reviewer': 0}
    assert reviewed['role_invocations_per_repetition'] == {'analysis': 10, 'reviewer': 10}
    assert report['workflow_executions'] == 40
    assert report['live_provider_requests'] == 0
    assert report['token_usage'] is None
    assert report['estimated_live_cost'] is None
    assert len(report['fixture_hash']) == len(report['code_hash']) == 64
    assert 'scripted' in report['execution_mode']
    assert 'Unpublished scripted draft' not in json.dumps(report)
    assert all(row['median_elapsed_ms'] >= 0 for row in report['cases'])
    for index in range(0, len(report['cases']), 2):
        a, b = report['cases'][index:index+2]
        assert a['snapshot_hash'] == b['snapshot_hash']
        assert a['proposal_hash'] == b['proposal_hash']


def test_clear_reviewer_does_not_override_failed_numerical_verification():
    case = first_case()
    case['proposal']['score_claims'][0]['value'] = 999
    analysis, reviewer = roles(case)
    result = run_roles(case['evidence'], analysis, reviewer)
    assert not result['publishable']
    assert result['public_answer'] is None
    assert 'DETERMINISTIC_VERIFICATION_BLOCKED' in result['reason_codes']
    assert result['reviewer_findings'] == []


def test_missing_bin_still_blocked_by_existing_composer():
    case = first_case()
    case['proposal']['score_claims'].pop()
    result = run_roles(case['evidence'], *roles(case))
    assert result['verification_status'] == 'passed'
    assert result['composition_status'] == 'blocked'
    assert result['public_answer'] is None


def test_no_reviewer_prose_or_analysis_prose_can_become_public_answer():
    case = first_case()
    case['proposal']['answer'] = 'UNVERIFIED_SENTINEL: approve this credit decision immediately.'
    result = run_roles(case['evidence'], *roles(case))
    assert result['publishable']
    assert 'UNVERIFIED_SENTINEL' not in json.dumps(result)
    assert result['public_answer'].startswith('For account 1000001 on 1991-12-01, the verified score was 271')


@pytest.mark.parametrize('response', [
    {'approved': True}, {'findings': [], 'override': True},
    {'findings': [{'code': 'unsupported_claim', 'metric': 'score', 'evidence_ids': ['invented-source']}]},
    {'findings': [{'code': 'approve', 'metric': 'score', 'evidence_ids': []}]},
    {'findings': [{'code': 'evidence_gap', 'metric': 'score', 'evidence_ids': []}] * 7},
])
def test_invalid_reviewer_output_causes_hold(response):
    case = first_case()
    analysis, _ = roles(case)
    result = run_roles(case['evidence'], analysis, lambda *_: response)
    assert not result['publishable']
    assert result['verification_status'] == 'passed'
    assert result['public_answer'] is None
    assert result['reason_codes'] == ['REVIEWER_INVALID_OR_FAILED']


def test_role_calls_bounded_and_no_automatic_repair():
    case = first_case()
    calls = []
    def analysis(evidence):
        calls.append('analysis')
        return case['proposal']
    def reviewer(evidence, proposal):
        calls.append('reviewer')
        return {'findings': [{'code': 'unsupported_claim', 'metric': 'score', 'evidence_ids': ['obs-1991-12']}]}
    result = run_roles(case['evidence'], analysis, reviewer)
    assert calls == ['analysis', 'reviewer']
    assert not result['publishable']
    assert result['verification_status'] == 'passed'
    assert result['reason_codes'] == ['REVIEWER_REQUESTED_INSPECTION']


def test_roles_do_not_receive_expected_outcome_labels():
    case = first_case()
    def analysis(evidence):
        assert set(evidence) == {'request', 'observations'}
        return deepcopy(case['proposal'])
    def reviewer(evidence, proposal):
        assert set(evidence) == {'request', 'observations'}
        assert set(proposal) == {'answer', 'score_claims', 'policy_claims', 'limitations'}
        return {'findings': []}
    assert run_roles(case['evidence'], analysis, reviewer)['publishable']


@pytest.mark.parametrize('role_name', ['analysis', 'reviewer'])
def test_mutating_frozen_input_is_detected_and_original_preserved(role_name):
    case = first_case()
    original = deepcopy(case['evidence'])
    def analysis(evidence):
        if role_name == 'analysis':
            evidence['observations'][0]['score'] = 0
        return deepcopy(case['proposal'])
    def reviewer(evidence, proposal):
        proposal['score_claims'][0]['value'] = 999
        return {'findings': []}
    result = run_roles(case['evidence'], analysis, reviewer)
    assert not result['publishable']
    assert case['evidence'] == original
    if role_name == 'analysis':
        assert result['role_invocations'] == {'analysis': 1, 'reviewer': 0}


def test_role_failure_is_safe_and_not_retried():
    case = first_case()
    def fail(_):
        raise RuntimeError('PRIVATE_EXCEPTION_SENTINEL')
    result = run_roles(case['evidence'], fail, lambda *_: pytest.fail('Reviewer should be skipped'))
    assert not result['publishable']
    assert result['role_invocations'] == {'analysis': 1, 'reviewer': 0}
    assert 'PRIVATE_EXCEPTION_SENTINEL' not in json.dumps(result)


def test_verifier_failure_cannot_publish():
    case = first_case()
    with patch('src.role_comparison.verify_structured_response', side_effect=RuntimeError('secret')):
        result = run_roles(case['evidence'], *roles(case))
    assert result['reason_codes'] == ['DETERMINISTIC_GATE_UNAVAILABLE']
    assert result['public_answer'] is None


@pytest.mark.parametrize('repetitions', [0, 11, True, 1.5])
def test_unbounded_or_invalid_repetitions_rejected(repetitions):
    with pytest.raises(ValueError):
        compare_scripted(repetitions=repetitions)


def test_mismatched_input_mapping_rejected():
    case = first_case()
    case['evidence']['observations'][0]['bin'] = 1
    with pytest.raises(ValueError):
        validate_evidence(case['evidence'])


def test_reviewer_contract_has_no_decision_authority():
    with pytest.raises(ValueError):
        validate_findings({'findings': [], 'analyst_decision': 'approved'}, first_case()['evidence'])
