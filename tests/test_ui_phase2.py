"""Read-only UI integration checks, including stable package binding."""
from fastapi.testclient import TestClient
from tests.test_api_phase2 import files, app, HEADERS, BODY
from src.case_application import implementation_hashes


def test_filter_precedes_pagination_and_rejects_invalid_status(files):
    with TestClient(app(files)) as client:
        first = client.post('/cases', headers=HEADERS, json=BODY).json()
        client.app.state.application.tick()
        second = client.post('/cases', headers={**HEADERS, 'Idempotency-Key': 'second'}, json=BODY).json()
        response = client.get('/cases?workflow_status=awaiting_review&limit=1', headers=HEADERS)
        assert response.status_code == 200
        assert [c['case_id'] for c in response.json()['items']] == [first['case_id']]
        assert not response.json()['has_more']
        queued = client.get('/cases?workflow_status=queued', headers=HEADERS).json()
        assert [c['case_id'] for c in queued['items']] == [second['case_id']]
        page = client.get('/cases?limit=1', headers=HEADERS).json()
        assert page['has_more']
        assert client.get('/cases?workflow_status=invalid', headers=HEADERS).status_code == 422
        assert client.get('/cases?workflow_status=held').status_code == 401


def test_ui_shell_and_assets_cannot_read_cases_without_auth(files):
    with TestClient(app(files)) as client:
        shell = client.get('/')
        # A source-only Python checkout may not have built the optional frontend.
        assert shell.status_code in (200, 503)
        assert shell.headers['cache-control'] == 'no-store'
        assert "frame-ancestors 'none'" in shell.headers['content-security-policy']
        assert client.get('/cases').status_code == 401
        assert client.get('/cases?workflow_status=queued').status_code == 401
        if shell.status_code == 200:
            assert 'Local API token' in shell.text
            js = client.get('/ui/app.js')
            assert js.status_code == 200
            assert js.headers['x-content-type-options'] == 'nosniff'
            assert client.get('/ui/../../src/api.py').status_code == 404


def test_m31_implementation_hashes_preserved():
    # Baseline e27561f; constants also work in a shallow CI checkout.
    expected = {'batch_scan.py': 'c76e9d016c018e1b9dcc04eeadbd06bf07f456388f2d03e8686b471541af0f20', 'policy_decisions.py': '11ce748d9a12599da00a5fd51ca2a15c9f356777fe3595324f4a90860e2954d6', 'temporal_policy.py': '2d821743972bb59f066f46ef7cb5bd4c170f799b451303b7624f377b04e07322', 'case_application.py': 'a15e7652497534276a486db7c545c9be838b3b7426be98e63fbb3c845dc4d523', 'review_workflow.py': '8169e69d9a0283f18666f0a9a96e5f24315d2845267eb6780de2ac31d027c45f', 'review_graph.py': '91996d6956b97a51f618f59ae620a21f76f42e49787af813c1248ae1f094e60a'}
    assert implementation_hashes() == expected
