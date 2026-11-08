"""Real HTTP review contract smoke: creates/decides synthetic cases; no live calls."""
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    token = os.environ['COPILOT_API_TOKEN']

    def call(path, body=None, expected=200):
        headers = {'Authorization': 'Bearer ' + token}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'Idempotency-Key': 'm33-' + str(uuid4())})
        request = Request('http://127.0.0.1:8000' + path, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
        try:
            with urlopen(request, timeout=15) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:
            status, raw = error.code, error.read()
        assert status == expected, (path, status, expected)
        return raw

    assert b'mountReview' in call('/ui/review.js')
    for account, verdict in [(1000003, 'approved'), (1000003, 'rejected'), (999999999, None)]:
        case = json.loads(call('/cases', dict(account_id=account, market=1,
                                             as_of_period='1991-12-01'), 202))
        route = '/cases/' + case['case_id']
        for _ in range(100):
            case = json.loads(call(route))
            if case['workflow_status'] not in ('queued', 'running'):
                break
            time.sleep(.1)
        assert case['workflow_status'] == ('awaiting_review' if verdict else 'held')
        body = dict(expected_revision=case['revision'], package_hash=case['package_hash'],
                    decision=verdict or 'approved', reason='M3.3 synthetic HTTP verification')
        if verdict:
            call(route+'/decisions', dict(body, reason='   '), 422)
            call(route+'/decisions', dict(body, expected_revision=0), 409)
            call(route+'/decisions', dict(body, package_hash='0'*64), 409)
            saved = json.loads(call(route+'/decisions', body))
            assert saved['workflow_status'] == 'completed' and saved['analyst_decision'] == verdict
            assert saved['package_hash'] == case['package_hash']
            call(route+'/decisions', body, 409)
            assert json.loads(call(route)) == saved
        else:
            call(route+'/decisions', body, 409)
        print(case['case_id'], verdict or 'held; decision blocked')
    print('Review assets, approve/reject, required reason, stale/hash/duplicate conflicts and held gate passed.')
    print('Three synthetic cases created; two demo decisions recorded. No LLM calls.')


if __name__ == '__main__':
    main()
