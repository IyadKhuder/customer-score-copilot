"""Direct observed-recovery answers and one-month-per-line presentation."""
import sqlite3
from types import SimpleNamespace

import pytest

from test_descriptive_window import service, review
from test_slack_listener_83 import Client, event
from src.slack_listener import format_result, SlackReviewListener


def test_partial_recovery_answer_follows_header_and_window(service):
    result=review(service,1000025)
    text=format_result(result,recovery_question=True)
    lines=text.splitlines()
    assert lines[0].startswith('Account 1000025')
    assert lines[1].startswith('Descriptive window: 1991-01 to 1991-12')
    assert lines[2].startswith('Yes, partially:')
    assert 'below its pre-drop level' in lines[2]
    assert result['status']=='no_signal_in_scope' and result['next_action']=='none'


@pytest.mark.parametrize('score',[350,355])
def test_full_observed_recovery_is_yes(service,score):
    with sqlite3.connect(service.db_path) as db:
        db.execute("UPDATE scores SET score=?,bin=? WHERE account_id=1000025 AND period='1991-12-01'",(score,1+score//50))
    result=review(service,1000025)
    assert result['observed_recovery_answer'].startswith('Yes:')
    assert 'back at or above' in result['observed_recovery_answer']
    assert 'formal reinstatement eligibility' in format_result(result,recovery_question=True)


def test_no_recovery_for_declining_history(service):
    assert review(service,1000003)['observed_recovery_answer'].startswith('No observed recovery')


def test_improvement_without_a_preceding_drop_is_not_called_recovery(service):
    answer=review(service,1000005)['observed_recovery_answer']
    assert 'score improved' in answer and 'no preceding drop is visible' in answer
    assert not answer.startswith('Yes')


def test_mixed_history_is_uncertain(service):
    assert review(service,1000001)['observed_recovery_answer'].startswith('I cannot establish')


def test_opposite_profile_is_distinguished(service):
    answer=review(service,1000026)['observed_recovery_answer']
    assert answer.startswith('No clear drop-and-recovery pattern')
    assert 'rise followed by decline' in answer


def test_missing_months_do_not_produce_a_false_negative(service):
    with sqlite3.connect(service.db_path) as db:
        db.execute("DELETE FROM scores WHERE account_id=1000025 AND period='1991-02-01'")
    answer=review(service,1000025)['observed_recovery_answer']
    assert answer.startswith('There is not enough monthly data')
    assert not answer.startswith(('Yes','No'))


def test_quality_flags_prevent_a_definitive_answer(service):
    answer=review(service,1000007)['observed_recovery_answer']
    assert answer.startswith('I cannot reliably determine') and 'data-quality' in answer


def test_missing_account_does_not_get_a_recovery_verdict(service):
    result=review(service,999)
    assert result['observed_recovery_answer'] is None
    assert 'was not found' in format_result(result,recovery_question=True)


def test_monthly_values_are_individual_lines(service):
    result=review(service,1000025)
    lines=format_result(result).splitlines()
    heading=lines.index('Reported scores (12-month descriptive window):')
    expected=[f'{p[:7]}: {s}' for p,s in zip(result['chart']['periods'],result['chart']['scores'])]
    assert lines[heading+1:heading+13]==expected
    assert not any(', 1991-' in line for line in lines)


def test_recovery_answer_only_appears_when_requested(service):
    result=review(service,1000025)
    assert 'Yes, partially:' not in format_result(result)
    assert 'Yes, partially:' in format_result(result,recovery_question=True)


def test_natural_recovery_question_routes_the_direct_answer(service):
    client=Client();errors=[]
    listener=SlackReviewListener(service,'C1',sleeper=lambda _:None,charts_enabled=False)
    body=event(text='<@BOT> Has account 1000025 recovered as of December 1991?')
    listener.handle(body,{'bot_user_id':'BOT'},client,SimpleNamespace(error=errors.append))
    listener.handle(body,{'bot_user_id':'BOT'},client,SimpleNamespace(error=errors.append))
    assert len(client.sent)==2 and not errors
    assert client.sent[1]['text'].splitlines()[2].startswith('Yes, partially:')
