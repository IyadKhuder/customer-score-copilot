"""New retry client against real API transactions and restart, without network."""
from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from src.api import create_app

spec = importlib.util.spec_from_file_location('submission_client', Path(__file__).resolve().parents[1] / 'deploy/azure/submit_case.py')
submission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(submission)
TOKEN = 'isolated-test-token-at-least-32-characters'


def test_client_recovers_committed_submission_after_response_loss_and_restart(tmp_path):
    scores = tmp_path / 'scores.db'
    with closing(sqlite3.connect(scores)) as db, db:
        db.execute('CREATE TABLE scores (account_id INTEGER, period TEXT, score INTEGER, bin INTEGER, market INTEGER)')
        db.executemany('INSERT INTO scores VALUES (?,?,?,?,?)',
                       [(1000003, f'1991-{m:02}-01', s, b, 1)
                        for m, s, b in [(9, 350, 7), (10, 320, 6), (11, 300, 6), (12, 265, 5)]])
    receipt = tmp_path / 'request.json'
    saved = submission.intent('https://example.org', receipt)
    path = tmp_path / 'cases.db'
    first_id = None
    calls = []
    class Response(io.BytesIO):
        status = 202
    class Opener:
        def open(self, request, timeout):
            nonlocal first_id
            headers = dict(request.header_items())
            # A fresh lifespan for each attempt proves recovery across application restarts.
            app = create_app(store_path=path, scores_path=scores, token=TOKEN, start_worker=False)
            with TestClient(app) as client:
                response = client.post('/cases', content=request.data, headers=headers)
                assert response.status_code == 202
                case = response.json()
                calls.append(case['case_id'])
                if first_id is None:
                    first_id = case['case_id']
                    raise TimeoutError()  # Commit succeeded, client never saw response.
                assert case['case_id'] == first_id
                assert len(client.get('/cases', headers=headers).json()['items']) == 1
                conflict = client.post('/cases', json=dict(submission.BODY, account_id=1000001), headers=headers)
                assert conflict.status_code == 409
                return Response(response.content)
    result = submission.submit(saved, TOKEN, Opener(), lambda _: None)
    assert calls == [first_id, first_id]
    assert result['case_id'] == first_id
    assert submission.intent('https://example.org', receipt)['key'] == saved['key']
