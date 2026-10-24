"""Scenario integration and boundaries for factual, deterministic summaries."""
import json
import sqlite3

import pytest

from src.synthetic_foundations import build_default_anchors
from src.slack_review_service import ReviewService
from src.slack_listener import format_result
from src.slack_summary import describe_scan


@pytest.fixture
def service(tmp_path):
    path = tmp_path/'scores.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE scores(account_id,period,score,bin,market)')
        conn.executemany('INSERT INTO scores VALUES(:account_id,:period,:score,:bin,:market)',
                         [r.as_dict() for r in build_default_anchors()])
    return ReviewService(path)


@pytest.mark.parametrize('account,phrase', [
    (1000001, 'broadly stable'), (1000002, 'improving'),
    (1000003, 'declining'), (1000004, 'sudden score drop'),
    (1000005, 'improving'), (1000006, 'possible data-quality issue'),
    (1000007, 'possible data-quality issue'), (1000008, 'severe score drop'),
    (1000009, 'not enough monthly data'), (1000010, 'requested month is missing'),
    (1000011, 'not enough monthly data'),
])
def test_real_anchor_descriptions(service, account, phrase):
    result = service.review(f'review account {account} as of 1991-12')
    text = format_result(result)
    assert phrase in text
    assert 'scenarios' not in json.dumps(result)
    assert 'scan_evidence' not in result and 'policy_decision' not in result
    assert 'WARNING_SIGNAL_TRIGGERED' not in text
    assert 'MISSING_AS_OF_OBSERVATION' not in text
    assert len(text) < 3000


def test_observations_are_exact_and_recovery_is_not_authorized(service):
    text = format_result(service.review('review account 1000005 as of 1991-12'))
    assert '1991-07: 125' in text and '1991-12: 175' in text
    assert 'Net change across those observations: +50 points.' in text
    assert 'does not establish recovery or reinstatement eligibility' in text


def test_history_is_cut_off_at_request_and_has_no_future_scores(service):
    text = format_result(service.review('review account 1000008 as of 1991-09'))
    assert '1991-10: 239' not in text
    assert 'severe score drop' not in text


def test_missing_interior_never_claims_stability(service):
    text = format_result(service.review('review account 1000009 as of 1991-12'))
    assert 'broadly stable' not in text
    assert '1991-10' in text  # missing-month disclosure


def test_quality_hold_is_not_a_final_risk_label(service):
    text = format_result(service.review('review account 1000007 as of 1991-12'))
    assert 'on hold until the data is checked' in text
    assert 'severe score drop detected' not in text
    assert 'No LLM was called' in text


def test_invalid_or_unsupported_history_has_no_summary():
    for status in ('failed', 'unsupported'):
        assert describe_scan({'scan_outcome': status}) == []


def test_unknown_account_still_has_direct_message(service):
    text = format_result(service.review('review account 1208 as of 1991-12'))
    assert text == ('Account ID 1208 was not found in the current database. '
                    'Please verify the account ID and try again.')


def test_pattern_follows_history_not_account_number(service):
    # Move a declining history to an arbitrary ID; no scenario index is consulted.
    with sqlite3.connect(service.db_path) as conn:
        conn.execute('UPDATE scores SET account_id=7721 WHERE account_id=1000003')
    text = format_result(service.review('review account 7721 as of 1991-12'))
    assert 'Score pattern: declining' in text
    assert 'Account 7721' in text
