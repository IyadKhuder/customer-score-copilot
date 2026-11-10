"""Recoverable offline case submission. Never sends analyst decisions or creates LLM notes."""
import argparse
import json
import os
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from uuid import UUID, uuid4

RETRYABLE = {502, 503, 504}
BODY = dict(account_id=1000003, market=1, as_of_period='1991-12-01')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect refused')


def origin(value):
    parts = urlsplit(value)
    local = parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost')
    if (parts.scheme != 'https' and not local) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError('Expected HTTPS origin (HTTP allowed only on loopback)')
    return value.rstrip('/')


def intent(base, path):
    base = origin(base)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist BEFORE contacting the server. Never regenerate on retry.
        with path.open('x', encoding='utf-8') as stream:
            json.dump(dict(schema=1, origin=base, key=str(uuid4()), payload=BODY), stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    saved = json.loads(path.read_text(encoding='utf-8-sig'))
    if saved.get('schema') != 1 or saved.get('origin') != base or saved.get('payload') != BODY:
        raise ValueError('Receipt does not match this origin and synthetic request')
    if str(UUID(saved['key'])) != saved['key']:
        raise ValueError('Invalid request identity')
    return saved


def submit(saved, token, opener=None, delay=time.sleep):
    if not token or len(token) < 32:
        raise ValueError('Configure the API token in the environment')
    opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    payload = json.dumps(saved['payload']).encode()
    for attempt in range(3):
        request = Request(origin(saved['origin']) + '/cases', data=payload,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Content-Type': 'application/json', 'Idempotency-Key': saved['key']})
        try:
            with opener.open(request, timeout=10) as response:
                if response.status != 202:
                    raise ValueError('Unexpected submission response; inspect the saved request')
                case = json.load(response)
            # A malformed response is ambiguous: keep the same receipt, never invent a new key.
            if (str(UUID(case['case_id'])) != case['case_id'] or
                    case['account_ids'] != [saved['payload']['account_id']] or
                    case['market'] != 1 or case['as_of_period'] != saved['payload']['as_of_period']):
                raise ValueError('Submission response does not match the saved request')
            return case
        except HTTPError as error:
            if error.code not in RETRYABLE:
                raise RuntimeError(f'Submission stopped: HTTP {error.code}; keep the receipt') from None
        except ssl.SSLError:
            raise RuntimeError('TLS failed; verify the endpoint before recovery') from None
        except URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                raise RuntimeError('TLS failed; verify the endpoint before recovery') from None
        except (TimeoutError, ConnectionError):
            pass
        if attempt < 2:
            delay(attempt + 1)
    raise RuntimeError('Three attempts exhausted; outcome unknown. Retain this receipt and rerun with it when ready.')


def run(base, path):
    saved = intent(base, path)
    case = submit(saved, os.environ.get('COPILOT_API_TOKEN', ''))
    # Receipt retains the original identity even if local response recording is interrupted.
    if saved.get('case_id') and saved['case_id'] != case['case_id']:
        raise RuntimeError('Server returned a different case for the saved identity')
    saved['case_id'] = case['case_id']
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(saved, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    print('Original case recovered:', case['case_id'], case['workflow_status'])
    print('No analyst decision sent. Keep this receipt for any subsequent submission recovery.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin')
    parser.add_argument('receipt', type=Path)
    args = parser.parse_args()
    run(args.origin, args.receipt)
