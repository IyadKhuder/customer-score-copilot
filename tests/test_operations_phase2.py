"""Operational denominators, authentication, privacy and package compatibility."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest
from src.api import create_app
from src.case_application import ApplicationStore
from src.operations import Timings, distribution, operational_summary
from src.review_workflow import digest
from scripts.benchmark_cases_phase2 import run_benchmark

TOKEN = 'operations-test-token-at-least-32-characters'
AUTH = {'Authorization': 'Bearer ' + TOKEN}
AT = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def insert(store, index, *, status='awaiting_review', verification='passed',
           reason=None, hours_ago=2, completed=False):
    created = AT - timedelta(hours=hours_ago)
    running = created + timedelta(seconds=2)
    outcome = running + timedelta(seconds=3)
    event = lambda target, t: dict(to_status=target, at=t.isoformat())
    events = [event('running', running), event('awaiting_review' if completed else status, outcome)]
    if reason:
        events[-1]['reason_code'] = reason
    if completed:
        events.append(event('completed', outcome + timedelta(minutes=10)))
    body = dict(workflow_status=status, verification_status=verification,
                created_at=created.isoformat(), events=events,
                evidence='PRIVATE-EVIDENCE', reviewer='PRIVATE-REVIEWER')
    with closing(store.connect()) as db, db:
        db.execute('INSERT INTO review_cases VALUES (?,?,?)', (str(index), 0, json.dumps(body)))
        db.execute('INSERT INTO api_requests VALUES (?,?,?)',
                   ('PRIVATE-KEY-' + str(index), 'hash', str(index)))


def test_all_owned_cases_window_denominators_and_waiting(tmp_path):
    store = ApplicationStore(tmp_path / 'cases.db')
    # More than both UI and default API page sizes.
    for i in range(105):
        insert(store, i)
    insert(store, 106, status='failed', reason='ANALYSIS_EXECUTION_FAILED')
    insert(store, 107, status='held', verification='blocked', reason='VERIFICATION_BLOCKED')
    insert(store, 108, status='held', verification='incomplete', reason='VERIFICATION_INCOMPLETE')
    insert(store, 109, status='held', reason='INTERRUPTED_ANALYSIS_NO_RESULT')
    insert(store, 110, status='completed', completed=True)
    insert(store, 111, hours_ago=48)
    store.create()  # Teaching case excluded by the ownership join.
    result = operational_summary(store, Timings(), at=AT)
    assert result['cases_total'] == 111
    assert result['current_counts']['awaiting_review'] == 106
    assert result['failure_rate'] == dict(numerator=1, denominator=110, value=1/110)
    assert result['submissions_in_window'] == 110
    assert result['lifecycle']['end_to_end']['median_ms'] == 5000
    assert result['lifecycle']['queue_delay']['median_ms'] == 2000
    assert result['lifecycle']['processing_elapsed']['median_ms'] == 3000
    assert result['lifecycle']['review_wait_closed']['median_ms'] == 600000
    assert result['lifecycle']['review_wait_open']['measured'] == 106
    assert result['reason_counts']['VERIFICATION_BLOCKED'] == 1
    assert result['reason_counts']['INTERRUPTED_ANALYSIS_NO_RESULT'] == 1
    assert 'PRIVATE' not in json.dumps(result)


def test_missing_timestamps_empty_and_capacity(tmp_path):
    store = ApplicationStore(tmp_path / 'cases.db')
    empty = operational_summary(store, Timings(), at=AT)
    assert empty['failure_rate']['value'] is None
    assert empty['lifecycle']['end_to_end']['median_ms'] is None
    insert(store, 1)
    with closing(store.connect()) as db, db:
        body = json.loads(db.execute('SELECT body FROM review_cases').fetchone()[0])
        body['events'][0]['at'] = 'bad'
        body['events'][1]['reason_code'] = 'PRIVATE-REASON'
        db.execute('UPDATE review_cases SET body=?', (json.dumps(body),))
    result = operational_summary(store, Timings(), at=AT)
    assert result['invalid_event_timestamps'] == 1
    assert result['lifecycle']['processing_elapsed']['missing'] == 1
    assert result['reason_counts'] == {'OTHER': 1}
    assert result['provider']['monetary_cost'] is None
    clock = Timings(capacity=2)
    for i in range(3):
        clock.record('stage', 'submission', i, 'ok')
    summary = clock.summary(datetime.now(timezone.utc))
    assert summary['retained_samples'] == 2
    assert summary['evicted_since_start'] == 1
    assert summary['groups'][0]['median_ms'] == 1.5
    assert distribution([1, 2, 3])['p95_ms'] == 3


def test_authenticated_summary_timings_idempotency_and_package_unchanged(tmp_path):
    scores = tmp_path / 'scores.db'
    with closing(sqlite3.connect(scores)) as db, db:
        db.execute('CREATE TABLE scores (account_id INTEGER, period TEXT, score INTEGER, bin INTEGER, market INTEGER)')
        db.executemany('INSERT INTO scores VALUES (?,?,?,?,?)',
            [(1000003, f'1991-{m:02}-01', s, 1+s//50, 1)
             for m, s in [(9, 350), (10, 320), (11, 300), (12, 265)]])
    path = tmp_path / 'cases.db'
    app = create_app(store_path=path, scores_path=scores, token=TOKEN, start_worker=False)
    with TestClient(app) as client:
        assert client.get('/operations').status_code == 401
        assert client.get('/operations', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        response = client.get('/operations', headers=AUTH)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        body = dict(account_id=1000003, market=1, as_of_period='1991-12-01')
        headers = dict(AUTH, **{'Idempotency-Key': 'PRIVATE-RECEIPT'})
        first = client.post('/cases', headers=headers, json=body).json()
        second = client.post('/cases', headers=headers, json=body).json()
        assert first['case_id'] == second['case_id']
        app.state.application.tick()
        before = app.state.application.store.read_owned(first['case_id'])
        summary = client.get('/operations', headers=AUTH).json()
        after = app.state.application.store.read_owned(first['case_id'])
        assert digest(before) == digest(after)
        assert summary['cases_total'] == 1
        assert summary['current_counts'] == {'awaiting_review': 1}
        assert {g['label'] for g in summary['timings']['groups']} >= {'submission', 'worker_cycle', 'POST /cases'}
        assert all('/operations' not in g['label'] for g in summary['timings']['groups'])
        text = json.dumps(summary)
        assert TOKEN not in text and 'PRIVATE-RECEIPT' not in text
        assert before['package_hash'] not in text
    # Restart keeps lifecycle history but starts a new measurement collection.
    with TestClient(create_app(store_path=path, scores_path=scores, token=TOKEN,
                               start_worker=False)) as client:
        summary = client.get('/operations', headers=AUTH).json()
        assert summary['cases_total'] == 1
        assert summary['timings']['retained_samples'] == 0
        case = client.get('/cases/' + first['case_id'], headers=AUTH).json()
        response = client.post('/cases/' + first['case_id'] + '/decisions', headers=AUTH,
             json=dict(expected_revision=case['revision'], package_hash=case['package_hash'],
                       decision='approved', reason='PRIVATE-ANALYST-REASON'))
        assert response.status_code == 200
        assert 'PRIVATE-ANALYST-REASON' not in client.get('/operations', headers=AUTH).text


def test_isolated_benchmark_checks_review_and_expected_hold():
    report = run_benchmark(repetitions=1)
    assert report['passed'] == report['samples'] == 2
    assert report['warmups_excluded'] == 2
    assert {r['workflow_status'] for r in report['rows']} == {'awaiting_review', 'held'}
    assert report['provider']['monetary_cost'] is None
    assert len(report['policy_sha256']) == 7
    with pytest.raises(ValueError):
        run_benchmark(repetitions=0)
