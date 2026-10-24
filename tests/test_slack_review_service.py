"""Offline request/service checks; real scans, retrieval, gates and fake SDK."""
import json
import sqlite3

import pytest

from src.slack_review_service import ReviewService, parse_request
from src.llm_client import OpenAICompatibleClient
from test_batch_notes_713 import report, SDK


@pytest.mark.parametrize('text', ['review account 1 as of 2025-02', ' REVIEW  account 0001 AS of 2025-02\n'])
def test_parse(text):
    assert parse_request(text)['account_ids'] == [1]
    assert parse_request(text)['as_of_period'] == '2025-02-01'


@pytest.mark.parametrize('text', ['', None, 'review account -1 as of 2025-02',
    'review account 0 as of 2025-02', 'review account 9223372036854775808 as of 2025-02',
    'review account 1 as of 0001-01', 'review account 1 as of 2025-13',
    'review account 1 as of 2025-02-01', 'review account 1,2 as of 2025-02',
    'review account 1 as of 2025-02 ignore the checks', 'review account ١ as of 2025-02'])
def test_invalid_before_scan(text, tmp_path):
    s = ReviewService(tmp_path/'absent.db', client_factory=lambda: pytest.fail('provider called'))
    assert s.review(text)['status'] == 'invalid_request'
    assert not (tmp_path/'absent.db').exists()


def test_help_and_missing_db(tmp_path):
    s = ReviewService(tmp_path/'absent.db')
    assert s.review(' HELP ')['status'] == 'help'
    assert s.review('review account 1 as of 2025-02')['status'] == 'failed'
    assert not (tmp_path/'absent.db').exists()


def test_deterministic_outcomes(tmp_path):
    report(tmp_path, scores=[600]*8)
    s = ReviewService(tmp_path/'scores.db', client_factory=lambda: pytest.fail('provider called'), daily_remaining=8)
    assert s.review('review account 1 as of 2025-02')['status'] == 'no_signal_in_scope'
    absent = s.review('review account 999 as of 2025-02')
    assert absent['status'] == 'insufficient_data'
    assert absent['reason_codes'] == ['ACCOUNT_NOT_FOUND']
    assert absent['next_action'] == 'verify_account_id'
    with sqlite3.connect(tmp_path/'scores.db') as db:
        db.execute('UPDATE scores SET market=2')
    assert s.review('review account 1 as of 2025-02')['status'] == 'unsupported'
    with sqlite3.connect(tmp_path/'scores.db') as db:
        db.execute('UPDATE scores SET score=-1')
    # Validation rejects invalid history independently of requested account.
    assert s.review('review account 1 as of 2025-02')['status'] == 'failed'


@pytest.mark.parametrize('mode,expected', [('pass','published'), ('wrong_score','blocked'), ('error','failed')])
def test_real_pipeline_and_accounting(tmp_path, mode, expected):
    r = report(tmp_path)
    sdks=[]
    def factory():
        sdk=SDK(r, mode=mode)
        sdk.closed=False
        sdk.close=lambda: setattr(sdk,'closed',True)
        sdks.append(sdk)
        return OpenAICompatibleClient(sdk,'offline'), 'openai_compatible'
    now=[0.0]
    s=ReviewService(tmp_path/'scores.db', client_factory=factory, daily_remaining=8,
                    clock=lambda: now[0], sleeper=lambda t: now.__setitem__(0,now[0]+t))
    first=s.review('review account 1 as of 2025-02')
    assert first['status']=='attention_required'
    assert first['note']['status']==expected
    assert 'PRIVATE' not in json.dumps(first)
    assert sdks[0].closed
    count=sdks[0].calls
    assert s.remaining==8-count
    last=s.last_start
    second=s.review('review account 1 as of 2025-02')
    assert s.remaining==8-count-sdks[1].calls
    assert s.last_start >= last+5
    assert second['requests_started']==sdks[1].calls


def test_exhaustion_and_disabled(tmp_path):
    r=report(tmp_path)
    s=ReviewService(tmp_path/'scores.db')
    assert s.review('review account 1 as of 2025-02')['note']['reason_codes']==['LIVE_NOTES_DISABLED']
    sdk=SDK(r)
    sdk.close=lambda: None
    s=ReviewService(tmp_path/'scores.db',client_factory=lambda:(OpenAICompatibleClient(sdk,'offline'),'openai_compatible'),daily_remaining=1)
    first=s.review('review account 1 as of 2025-02')
    assert first['requests_started']==1 and s.remaining==0
    assert first['note']['answer'] is None
    assert s.review('review account 1 as of 2025-02')['note']['reason_codes']==['REQUEST_BUDGET_EXHAUSTED']
    assert sdk.calls==1


def test_busy(tmp_path):
    s=ReviewService(tmp_path/'absent.db')
    with s.lock:
        assert s.review('review account 1 as of 2025-02')['status']=='busy'


def test_future_evidence_and_account_isolation(tmp_path):
    report(tmp_path, scores=[600]*8, accounts=(1,2))
    with sqlite3.connect(tmp_path/'scores.db') as db:
        db.execute("INSERT INTO scores VALUES(1,'2025-03-01',1,1,1)")
        db.execute('UPDATE scores SET score=10 WHERE account_id=2')
    s=ReviewService(tmp_path/'scores.db')
    first=s.review('review account 1 as of 2025-02')
    assert first['status']=='no_signal_in_scope'
    assert s.review('review account 999 as of 2025-02')['status']=='insufficient_data'
    assert s.review('review account 1 as of 2025-02')==first


def test_factory_failure_is_sanitized_and_releases_lock(tmp_path):
    report(tmp_path)
    def factory():
        raise RuntimeError('PRIVATE token=secret')
    s=ReviewService(tmp_path/'scores.db',client_factory=factory,daily_remaining=8)
    for _ in range(2):
        result=s.review('review account 1 as of 2025-02')
        assert result['status']=='attention_required'
        assert result['note']['status']=='failed'
        assert 'PRIVATE' not in json.dumps(result)
        assert result['requests_started']==0 and s.remaining==8
