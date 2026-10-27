"""Shared chart/text calendar, observed reversals and separate policy outcomes."""
from copy import deepcopy
import re
import sqlite3

import pytest

from src.batch_scan import scan_batch
from src.score_chart import display_periods, prepare_chart
from src.slack_summary import describe_scan
from src.slack_review_service import ReviewService
from src.synthetic_dataset import build_account, profile_config


@pytest.fixture
def service(tmp_path):
    db=tmp_path/'scores.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE scores(account_id,period,score,bin,market)')
        for ordinal in range(1,27):
            rows,_=build_account(profile_config('showcase'),1,ordinal)
            conn.executemany('INSERT INTO scores VALUES(:account_id,:period,:score,:bin,:market)',
                             [r.as_dict() for r in rows])
    return ReviewService(db)


def review(service, account, month='1991-12'):
    return service.review(f'review account {account} as of {month}')


@pytest.mark.parametrize('ordinal',range(1,27))
def test_every_account_reports_the_same_observations_as_its_chart(service,ordinal):
    result=review(service,1000000+ordinal)
    text='\n'.join(result['score_summary'])
    chart=result['chart']
    assert chart['periods']==display_periods('1991-12-01')
    assert '1991-01 to 1991-12 (12 calendar months, matching the chart)' in text
    assert 'does not describe earlier events' in text
    reported=dict((period,int(score)) for period,score in re.findall(r'(1991-[0-9]{2}): ([0-9]+)',text))
    expected={p[:7]:s for p,s in zip(chart['periods'],chart['scores']) if s is not None}
    assert reported==expected


def test_partial_recovery_answers_the_observed_question(service):
    result=review(service,1000025);text='\n'.join(result['score_summary'])
    assert 'drop followed by partial recovery' in text
    assert '350 in 1991-04 to 220 in 1991-05 (-130 points)' in text
    assert '+89 points to 309 in 1991-12; still 41 points below' in text
    assert 'Net change across those observations: -37 points.' in text
    assert result['status']=='no_signal_in_scope' and result['next_action']=='none'
    assert 'does not establish formal reinstatement eligibility' in text


def test_rise_then_decline_is_not_a_recovery(service):
    text='\n'.join(review(service,1000026)['score_summary'])
    assert 'rise followed by gradual decline' in text
    assert '222 in 1991-04 to 352 in 1991-05 (+130 points)' in text
    assert '-89 points to 263 in 1991-12; still 41 points above' in text
    assert 'partial recovery' not in text


@pytest.mark.parametrize('latest,expected',[(350,'back at the pre-drop score'),(355,'5 points above the pre-drop score')])
def test_recovery_reaches_or_exceeds_previous_level(service,latest,expected):
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("UPDATE scores SET score=?,bin=? WHERE account_id=1000025 AND period='1991-12-01'",(latest,1+latest//50))
    text='\n'.join(review(service,1000025)['score_summary'])
    assert 'drop followed by recovery to or above' in text and expected in text


def test_missing_older_month_does_not_invent_a_twelve_month_pattern(service):
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("DELETE FROM scores WHERE account_id=1000025 AND period='1991-02-01'")
    result=review(service,1000025);text='\n'.join(result['score_summary'])
    assert result['coverage_complete'] is True  # policy baseline remains complete
    assert result['chart']['scores'][1] is None
    assert 'full twelve-month trend' in text and 'Missing descriptive months: 1991-02' in text
    assert 'partial recovery' not in text
    assert 'Descriptive coverage: 11 of 12' in text
    from src.slack_listener import format_result
    assert 'Data available for the required policy review period: complete.' in format_result(result)


@pytest.mark.parametrize('month',['1991-01','1991-05','1991-09','1991-12'])
def test_rolling_calendar_and_cutoff(service,month):
    result=review(service,1000025,month)
    text='\n'.join(result['score_summary']);chart=result['chart']
    assert chart['periods']==display_periods(month+'-01')
    for period,score in zip(chart['periods'],chart['scores']):
        if score is not None:assert f'{period[:7]}: {score}' in text
    if month<'1991-06':
        assert 'Subsequent recovery' not in text and '309 in 1991-12' not in text


def test_events_before_window_and_after_cutoff_cannot_change_description(service):
    before=review(service,1000025,'1991-09')
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("UPDATE scores SET score=400,bin=9 WHERE account_id=1000025 AND (period<'1990-10-01' OR period>'1991-09-01')")
    after=review(service,1000025,'1991-09')
    assert before['score_summary']==after['score_summary'] and before['chart']==after['chart']


def test_policy_result_and_snapshot_are_not_changed_by_description(service):
    for account in (1000003,1000007,1000008,1000025,1000026):
        request=dict(schema_version=1,account_ids=[account],market=1,as_of_period='1991-12-01')
        result=scan_batch(request,service.db_path)['results'][0];original=deepcopy(result)
        describe_scan(result)
        assert result==original
        public=review(service,account)
        assert public['status']==result['scan_outcome']
        assert public['reason_codes']==result['reason_codes']
        assert public['next_action']==result['next_action']
        assert public['coverage_complete']==result['coverage_complete']


def test_description_is_not_tied_to_showcase_account_id(service):
    with sqlite3.connect(service.db_path) as conn:
        conn.execute('UPDATE scores SET account_id=789 WHERE account_id=1000025')
    text='\n'.join(review(service,789)['score_summary'])
    assert 'drop followed by partial recovery' in text


def test_equal_size_oscillations_are_not_called_gradual_recovery():
    periods=display_periods('1991-12-01')
    result=dict(scan_outcome='no_signal_in_scope',reason_codes=[],as_of_period='1991-12-01',policy_decision={},
                scan_evidence=[dict(period=p,score=225+80*(i%2),bin=5+(i%2),market=1) for i,p in enumerate(periods)])
    text='\n'.join(describe_scan(result))
    assert 'partial recovery' not in text and 'rise followed by gradual decline' not in text


@pytest.mark.parametrize('account,phrase',[(1000013,'overall improving'),(1000014,'overall declining')])
def test_small_prefix_fluctuations_do_not_obscure_overall_trend(service,account,phrase):
    assert phrase in '\n'.join(review(service,account)['score_summary'])


def test_recovery_question_shows_observations_before_formal_qualification(service):
    from types import SimpleNamespace
    from src.slack_listener import SlackReviewListener
    from test_slack_listener_83 import Client, event
    client=Client()
    listener=SlackReviewListener(service,'C1',sleeper=lambda _:None,charts_enabled=False)
    listener.handle(event(text='<@BOT> Has account 1000025 recovered as of December 1991?'),
                    {'bot_user_id':'BOT'},client,SimpleNamespace(error=lambda _:None))
    answer=client.sent[1]['text']
    assert answer.index('drop followed by partial recovery') < answer.index('This review describes observed score changes')
