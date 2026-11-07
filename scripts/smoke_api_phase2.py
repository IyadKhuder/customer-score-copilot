"""Real HTTP, two-phase restart demonstration; only offline synthetic cases.

Run prepare, recreate the server with the same durable volume, then run resume.
The state file contains case identifiers and hashes, never authentication tokens.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--state', default='generated/m31-smoke.json')
    parser.add_argument('phase', choices=['prepare', 'resume'])
    args = parser.parse_args()
    token = os.environ['COPILOT_API_TOKEN']
    base = args.base_url.rstrip('/')

    def call(method, route, body=None, key=None, expected=200):
        headers = {'Authorization': 'Bearer ' + token}
        if key:
            headers['Idempotency-Key'] = key
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(base + route, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=15) as response:
                status, payload = response.status, json.load(response)
        except HTTPError as error:
            status, payload = error.code, json.load(error)
        assert status == expected, (method, route, status, payload)
        return payload

    for attempt in range(60):
        try:
            call('GET', '/health/ready')
            break
        except Exception:
            if attempt == 59:
                raise
            time.sleep(.5)
    state_path = Path(args.state)
    if args.phase == 'prepare':
        assert not state_path.exists(), 'Use a fresh state path for a new demonstration'
        key = 'smoke-' + str(uuid4())
        body = dict(account_id=1000003, market=1, as_of_period='1991-12-01')
        case = call('POST', '/cases', body, key, 202)
        for _ in range(100):
            case = call('GET', '/cases/' + case['case_id'])
            if case['workflow_status'] != 'queued' and case['workflow_status'] != 'running':
                break
            time.sleep(.1)
        assert case['workflow_status'] == 'awaiting_review', case
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(dict(key=key, body=body, case=case), indent=2)+'\n')
        print('PASS: submitted, processed and paused for analyst review. Restart now; preserve the volume.')
    else:
        state = json.loads(state_path.read_text())
        case = call('GET', '/cases/' + state['case']['case_id'])
        assert case == state['case'], 'Saved review package changed across restart'
        replay = call('POST', '/cases', state['body'], state['key'], 202)
        assert replay == case
        route = '/cases/' + case['case_id']
        decision = dict(expected_revision=case['revision'], package_hash=case['package_hash'],
                        decision='approved', reason='Reviewed offline synthetic restart demonstration')
        call('POST', route+'/decisions', dict(decision, expected_revision=0), expected=409)
        saved = call('POST', route+'/decisions', decision)
        assert saved['workflow_status'] == 'completed' and saved['analyst_decision'] == 'approved'
        assert saved['package_hash'] == case['package_hash']
        call('POST', route+'/decisions', decision, expected=409)
        approved = call('GET', route+'/evidence')
        assert approved == saved and 'decision' not in approved and 'events' not in approved
        print('PASS: restart preserved package and idempotency; stale/duplicate decisions rejected; approved evidence read.')


if __name__ == '__main__':
    main()
