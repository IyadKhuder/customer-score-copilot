"""Read-only UI checks plus explicit offline demo case creation; no decisions/LLMs."""
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    base = 'http://127.0.0.1:8000'
    token = os.environ['COPILOT_API_TOKEN']

    def call(path, body=None, authenticated=True):
        headers = {'Authorization': 'Bearer ' + token} if authenticated else {}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'Idempotency-Key': 'm32-' + str(uuid4())})
        request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers)
        with urlopen(request, timeout=15) as response:
            return response.read(), response.headers

    shell, headers = call('/', authenticated=False)
    assert b'Case evidence' in shell
    assert headers['Cache-Control'] == 'no-store'
    for asset in ('app.js', 'model.js', 'view.js', 'style.css'):
        payload, _ = call('/ui/' + asset, authenticated=False)
        assert payload
    try:
        call('/cases', authenticated=False)
        raise AssertionError('Unauthenticated queue was accepted')
    except HTTPError as error:
        assert error.code == 401
    for account in (1000003, 1000010, 999999999):
        raw, _ = call('/cases', dict(account_id=account, market=1, as_of_period='1991-12-01'))
        case = json.loads(raw)
        for _ in range(100):
            raw, _ = call('/cases/' + case['case_id'])
            case = json.loads(raw)
            if case['workflow_status'] not in ('queued', 'running'):
                break
            time.sleep(.1)
        assert case['workflow_status'] not in ('queued', 'running')
        assert case['policy_memos'] and case['limitations']
        print(account, case['case_id'], case['workflow_status'], case['verification_status'])
    raw, _ = call('/cases?workflow_status=held&limit=1')
    assert all(item['workflow_status'] == 'held' for item in json.loads(raw)['items'])
    print('UI shell/assets, authenticated reads, frozen evidence and held filter passed.')
    print('No analyst decisions or live provider calls were made.')


if __name__ == '__main__':
    main()
