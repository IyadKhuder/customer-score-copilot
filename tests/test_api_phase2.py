"""HTTP/application contract checks using real LangGraph checkpoints and SQLite."""
from contextlib import closing
import copy
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from src.api import create_app
from src.case_application import CaseApplication, analyze_frozen, implementation_hashes
from src.review_graph import GRAPH_VERSION, config, open_graph
from src.review_workflow import TransitionError

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'test-token-with-at-least-32-characters'
HEADERS = {'Authorization': 'Bearer ' + TOKEN, 'Idempotency-Key': 'case-1'}
BODY = {'account_id': 1000003, 'market': 1, 'as_of_period': '1991-12-01'}


@pytest.fixture
def files(tmp_path):
    scores = tmp_path / 'scores.db'
    with closing(sqlite3.connect(scores)) as db, db:
        db.execute('CREATE TABLE scores (account_id INTEGER, period TEXT, score INTEGER, bin INTEGER, market INTEGER)')
        db.executemany('INSERT INTO scores VALUES (?,?,?,?,?)',
                       [(1000003, f'1991-{m:02}-01', s, b, 1) for m, s, b in
                        [(9, 350, 7), (10, 320, 6), (11, 300, 6), (12, 265, 5)]])
    return tmp_path / 'cases.db', scores


def app(files, worker=False):
    return create_app(store_path=files[0], scores_path=files[1], token=TOKEN, start_worker=worker)


def finished(client, case_id):
    for _ in range(100):
        case = client.get('/cases/' + case_id, headers=HEADERS).json()
        if case['workflow_status'] not in ('queued', 'running'):
            return case
        time.sleep(.03)
    raise AssertionError('Worker did not finish')


def decision(case, value='approved'):
    return dict(expected_revision=case['revision'], package_hash=case['package_hash'],
                decision=value, reason='Reviewed recorded evidence')


def test_authentication_covers_all_case_operations(files):
    with TestClient(app(files)) as client:
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').status_code == 503
        for method, url, kwargs in [
            ('get', '/cases', {}), ('get', '/cases/x', {}), ('get', '/cases/x/evidence', {}),
            ('get', '/openapi.json', {}), ('post', '/cases', {'json': BODY}),
            ('post', '/cases/x/decisions', {'json': dict(expected_revision=2, package_hash='a'*64,
                                                       decision='approved', reason='reviewed')})]:
            assert getattr(client, method)(url, **kwargs).status_code == 401
        assert client.get('/cases', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.get('/openapi.json', headers=HEADERS).status_code == 200


@pytest.mark.parametrize('patch', [dict(account_id=True), dict(account_id=0), dict(account_id=2**63),
                                  dict(market=True), dict(market=2), dict(as_of_period='1991-12'),
                                  dict(as_of_period='1991-12-02'), dict(live_notes=True)])
def test_input_scope_is_bounded(files, patch):
    with TestClient(app(files)) as client:
        assert client.post('/cases', json=dict(BODY, **patch), headers=HEADERS).status_code == 422
        assert client.get('/cases', headers=HEADERS).json()['items'] == []


def test_body_bounds_and_safe_errors(files):
    with TestClient(app(files)) as client:
        assert client.post('/cases', content='x'*8193, headers=HEADERS).status_code == 413
        response = client.post('/cases', json=dict(BODY, secret='private-content'), headers=HEADERS)
        assert 'private-content' not in response.text
        assert client.get('/cases/invalid', headers=HEADERS).status_code == 404
        assert client.get('/cases/00000000-0000-0000-0000-000000000000', headers=HEADERS).status_code == 404
        assert client.get('/cases?limit=101', headers=HEADERS).status_code == 422
        assert client.post('/cases', json=BODY, headers={'Authorization': 'Bearer '+TOKEN}).status_code == 422


def test_durable_idempotency_and_frozen_input(files):
    with TestClient(app(files)) as client:
        first = client.post('/cases', json=BODY, headers=HEADERS)
        assert first.status_code == 202
        case = first.json()
        assert case['workflow_status'] == 'queued'
        assert first.headers['Location'] == '/cases/' + case['case_id']
    # No source DB is needed to replay the already committed request.
    files[1].unlink()
    with TestClient(app(files)) as client:
        replay = client.post('/cases', json=BODY, headers=HEADERS)
        assert replay.status_code == 202
        assert replay.json() == case
        conflict = client.post('/cases', json=dict(BODY, account_id=1000001), headers=HEADERS)
        assert conflict.status_code == 409
        assert len(client.get('/cases', headers=HEADERS).json()['items']) == 1
        service = client.app.state.application
        service.tick()
        saved = client.get(first.headers['Location'], headers=HEADERS).json()
        assert saved['workflow_status'] == 'awaiting_review'
        assert saved['analysis']['scan']['scan_evidence'] == case['evidence']


@pytest.mark.parametrize('value', ['approved', 'rejected'])
def test_decisions_bound_to_saved_package_and_server_identity(files, value):
    with TestClient(app(files), raise_server_exceptions=True) as client:
        case = client.post('/cases', json=BODY, headers=HEADERS).json()
        service = client.app.state.application
        service.tick()
        case = client.get('/cases/'+case['case_id'], headers=HEADERS).json()
        assert case['workflow_status'] == 'awaiting_review'
        route = '/cases/'+case['case_id']
        assert client.get(route+'/evidence', headers=HEADERS).status_code == 409
        for patch in [dict(expected_revision=0), dict(package_hash='0'*64)]:
            assert client.post(route+'/decisions', json=dict(decision(case), **patch), headers=HEADERS).status_code == 409
        forged = dict(decision(case), reviewer='someone-else')
        assert client.post(route+'/decisions', json=forged, headers=HEADERS).status_code == 422
        response = client.post(route+'/decisions', json=decision(case, value), headers=HEADERS)
        assert response.status_code == 200
        saved = response.json()
        assert saved['workflow_status'] == 'completed'
        assert saved['analyst_decision'] == value
        assert saved['package_hash'] == case['package_hash']
        assert 'Reviewed recorded evidence' not in response.text
        assert 'demo-analyst' not in response.text
        assert 'events' not in saved and 'decision' not in saved
        assert len(saved['policy_memos']) == 7
        import hashlib
        for memo in saved['policy_memos']:
            assert hashlib.sha256(memo['text'].encode('utf-8')).hexdigest() == memo['sha256']
        assert client.post(route+'/decisions', json=decision(case, value), headers=HEADERS).status_code == 409
        assert client.get(route+'/evidence', headers=HEADERS).status_code == (200 if value=='approved' else 409)
    with TestClient(app(files)) as client:
        client.app.state.application.tick()
        assert client.get(route, headers=HEADERS).json() == saved
        audit = client.app.state.application.store.read(case['case_id'])['decision']
        assert audit['reviewer'] == 'demo-analyst'


def test_held_scan_retains_deterministic_evidence(files):
    with TestClient(app(files)) as client:
        case = client.post('/cases', json=dict(BODY, account_id=999), headers=HEADERS).json()
        client.app.state.application.tick()
        held = client.get('/cases/'+case['case_id'], headers=HEADERS).json()
        assert held['workflow_status'] == 'held'
        assert held['verification_status'] == 'incomplete'
        assert held['analysis']['scan']['reason_codes'] == ['ACCOUNT_NOT_FOUND']
        assert client.post('/cases/'+case['case_id']+'/decisions', json=dict(expected_revision=held['revision'],
            package_hash=held['package_hash'], decision='approved', reason='reviewed'), headers=HEADERS).status_code == 409


def test_interrupted_analysis_is_held_without_replay(files):
    service = CaseApplication(*files, ROOT)
    case = service.submit(dict(schema_version=1, account_ids=[1000003], market=1,
                               as_of_period='1991-12-01'), 'test')
    with open_graph(files[0], analyzer=analyze_frozen) as (store, graph):
        graph.invoke(dict(case_id=case['case_id'], snapshot_hash=case['snapshot_hash'],
                          graph_version=GRAPH_VERSION), config(case['case_id']), durability='sync')
    service.store.transition(case['case_id'], 0, 'running')
    restarted = CaseApplication(*files, ROOT)
    restarted.tick()
    saved = restarted.store.read(case['case_id'])
    assert saved['workflow_status'] == 'held'
    assert saved['analysis'] is None
    assert saved['events'][-1]['reason_code'] == 'INTERRUPTED_ANALYSIS_NO_RESULT'


def test_queued_case_recovers_without_graph_checkpoint(files):
    service = CaseApplication(*files, ROOT)
    case = service.submit(dict(schema_version=1, account_ids=[1000003], market=1,
                               as_of_period='1991-12-01'), 'test')
    restarted = CaseApplication(*files, ROOT)
    restarted.tick()
    assert restarted.store.read(case['case_id'])['workflow_status'] == 'awaiting_review'


def test_implementation_change_blocks_queued_work_and_decisions(files, monkeypatch):
    service = CaseApplication(*files, ROOT)
    request = dict(schema_version=1, account_ids=[1000003], market=1, as_of_period='1991-12-01')
    ready = service.submit(request, 'ready')
    service.tick()
    queued = service.submit(request, 'queued')
    monkeypatch.setattr('src.case_application.implementation_hashes', lambda: {'changed': 'code'})
    service.tick()
    assert service.store.read(queued['case_id'])['workflow_status'] == 'held'
    package = service.store.read(ready['case_id'])
    with pytest.raises(TransitionError):
        service.decide(ready['case_id'], dict(decision(package), case_id=ready['case_id'], reviewer='demo-analyst'))


def test_worker_lifecycle_and_process_lease(files):
    with TestClient(app(files, worker=True)) as client:
        assert client.get('/health/ready').status_code == 200
        case = client.post('/cases', json=BODY, headers=HEADERS).json()
        assert finished(client, case['case_id'])['workflow_status'] == 'awaiting_review'
        with pytest.raises(OSError):
            with TestClient(app(files)):
                pass
    with TestClient(app(files, worker=True)) as client:
        saved = finished(client, case['case_id'])
        assert saved['workflow_status'] == 'awaiting_review'


def test_missing_source_is_safe_and_not_enqueued(files):
    files[1].unlink()
    with TestClient(app(files)) as client:
        response = client.post('/cases', json=BODY, headers=HEADERS)
        assert response.status_code == 503
        assert str(files[1]) not in response.text
        assert client.get('/cases', headers=HEADERS).json()['items'] == []


def test_configuration_fails_closed(files):
    with pytest.raises(ValueError):
        create_app(token='')
    with pytest.raises(ValueError):
        create_app(token=TOKEN, store_path=files[1], scores_path=files[1])


def test_concurrent_duplicate_submissions_insert_one_case(files):
    from concurrent.futures import ThreadPoolExecutor
    service = CaseApplication(*files, ROOT)
    request = dict(schema_version=1, account_ids=[1000003], market=1, as_of_period='1991-12-01')
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: service.submit(request, 'concurrent')['case_id'], range(8)))
    assert len(set(ids)) == 1
    assert service.store.ids() == [ids[0]]


def test_policy_bytes_remain_frozen_after_sources_disappear(files, tmp_path):
    import shutil
    root = tmp_path/'source'
    shutil.copytree(ROOT/'policy_memos', root/'policy_memos')
    service = CaseApplication(*files, root)
    case = service.submit(dict(schema_version=1, account_ids=[1000003], market=1,
                              as_of_period='1991-12-01'), 'frozen-policy')
    expected = {p.name: p.read_bytes().hex() for p in (root/'policy_memos').glob('*.md')}
    shutil.rmtree(root)
    service.tick()
    saved = service.store.read(case['case_id'])
    assert saved['workflow_status'] == 'awaiting_review'
    assert saved['evidence']['policy_bytes'] == expected


def test_analysis_and_decision_commit_checkpoint_gaps_recover(files, monkeypatch):
    service = CaseApplication(*files, ROOT)
    case = service.submit(dict(schema_version=1, account_ids=[1000003], market=1,
                              as_of_period='1991-12-01'), 'gap')
    with open_graph(files[0]) as (store, graph):
        graph.invoke(dict(case_id=case['case_id'], snapshot_hash=case['snapshot_hash'],
                          graph_version=GRAPH_VERSION), config(case['case_id']), durability='sync')
    service.store.transition(case['case_id'], 0, 'running')
    analysis, verification = analyze_frozen(case['evidence'])
    saved = service.store.transition(case['case_id'], 1, 'awaiting_review',
                                     analysis=analysis, verification=verification)
    def forbidden(evidence):
        raise AssertionError('Saved analysis must not rerun')
    monkeypatch.setattr('src.case_application.analyze_frozen', forbidden)
    service.tick()
    assert service.store.read(case['case_id']) == saved
    payload = dict(decision(saved), case_id=saved['case_id'], reviewer='demo-analyst')
    completed = service.store.decide(case['case_id'], payload)
    restarted = CaseApplication(*files, ROOT)
    restarted.tick()
    assert restarted.store.read(case['case_id']) == completed
    with open_graph(files[0]) as (store, graph):
        assert not graph.get_state(config(case['case_id'])).next


def test_worker_failure_is_reported_by_readiness(files, monkeypatch):
    def fail(self):
        raise RuntimeError('private internal diagnostic')
    monkeypatch.setattr(CaseApplication, 'tick', fail)
    with TestClient(app(files, worker=True)) as client:
        client.app.state.application.thread.join(timeout=2)
        response = client.get('/health/ready')
        assert response.status_code == 503
        assert 'private internal diagnostic' not in response.text
