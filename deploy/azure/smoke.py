"""HTTPS smoke / persistence readback. Uses only stdlib and an in-memory env token."""
import argparse
import json
import os
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from uuid import uuid4

FIELDS = ('case_id', 'package_hash', 'revision', 'workflow_status', 'analyst_decision')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Unexpected redirect; refusing to forward credentials')


def run(base, record, verify):
    parts = urlsplit(base)
    if (parts.username or parts.password or parts.query or parts.fragment or
            parts.path not in ('', '/') or not parts.hostname):
        raise ValueError('Provide an origin only, without credentials, path or query')
    local = parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost')
    if parts.scheme != 'https' and not local:
        raise ValueError('Remote origin must use HTTPS')
    token = os.environ['COPILOT_API_TOKEN']
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def call(path, payload=None, expected=200, authenticated=True):
        headers = {'Authorization': 'Bearer ' + token} if authenticated else {}
        data = None
        if payload is not None:
            headers.update({'Content-Type': 'application/json', 'Idempotency-Key': str(uuid4())})
            data = json.dumps(payload).encode()
        req = Request(base.rstrip('/') + path, data=data, headers=headers)
        try:
            with opener.open(req, timeout=20) as response:
                status, body = response.status, response.read()
        except HTTPError as error:
            status, body = error.code, error.read()
        if status != expected:
            raise RuntimeError(f'{path}: expected HTTP {expected}, received {status}')
        return json.loads(body) if body else None

    call('/health/ready', authenticated=False)
    call('/cases', expected=401, authenticated=False)
    if verify:
        saved = json.loads(record.read_text(encoding='utf-8-sig'))
        fresh = call('/cases/' + saved['case_id'])
        if {key: fresh[key] for key in FIELDS} != saved:
            raise RuntimeError('Saved case identity/hash/revision/status/decision changed')
        print('Saved package and decision readback PASS:', saved['case_id'])
        return
    if record.exists():
        raise ValueError('Record already exists; use --verify or a new path')
    case = call('/cases', dict(account_id=1000003, market=1, as_of_period='1991-12-01'), 202)
    route = '/cases/' + case['case_id']
    for _ in range(100):
        case = call(route)
        if case['workflow_status'] not in ('queued', 'running'):
            break
        time.sleep(0.3)
    if case['workflow_status'] != 'awaiting_review':
        raise RuntimeError('Synthetic case did not reach awaiting_review')
    decision = dict(expected_revision=case['revision'], package_hash=case['package_hash'],
                    decision='approved', reason='M3.4 synthetic hosting verification')
    call(route + '/decisions', decision, expected=401, authenticated=False)
    saved = call(route + '/decisions', decision)
    if saved['analyst_decision'] != 'approved' or saved['workflow_status'] != 'completed':
        raise RuntimeError('Decision did not complete')
    if saved['package_hash'] != case['package_hash']:
        raise RuntimeError('Package hash changed')
    call(route + '/decisions', decision, expected=409)
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open('x', encoding='utf-8') as stream:
        json.dump({key: saved[key] for key in FIELDS}, stream, indent=2)
        stream.write('\n')
    print('Health, unauthorized read/decision, review and duplicate rejection PASS:', saved['case_id'])
    print('One synthetic case created and approved; no LLM calls. Receipt:', record)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin')
    parser.add_argument('record', type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    run(args.origin, args.record, args.verify)
