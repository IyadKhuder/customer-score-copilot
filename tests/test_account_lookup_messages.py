"""Unknown account and future-only history must remain distinct."""
import sqlite3
from src.slack_review_service import ReviewService
from src.slack_listener import format_result
from test_batch_notes_713 import report


def test_absent_id_is_actionable_and_never_calls_provider(tmp_path):
    report(tmp_path)
    def forbidden():
        raise AssertionError('Provider called')
    service = ReviewService(tmp_path/'scores.db', client_factory=forbidden, daily_remaining=8)
    result = service.review('review account 1208 as of 2025-02')
    assert result['reason_codes'] == ['ACCOUNT_NOT_FOUND']
    assert result['next_action'] == 'verify_account_id'
    assert service.remaining == 8
    assert format_result(result) == ('Account ID 1208 was not found in the current database. '
                                     'Please verify the account ID and try again.')


def test_future_only_history_does_not_mean_unknown_id(tmp_path):
    report(tmp_path)
    with sqlite3.connect(tmp_path/'scores.db') as db:
        db.execute("INSERT INTO scores VALUES(1208,'2025-03-01',200,5,1)")
    result = ReviewService(tmp_path/'scores.db').review('review account 1208 as of 2025-02')
    assert result['reason_codes'] == ['NO_HISTORY_AS_OF']
    assert result['next_action'] == 'gather_evidence'
    message = format_result(result)
    assert 'no observations are available on or before the requested month' in message
    assert 'not found' not in message
    assert '200' not in message
