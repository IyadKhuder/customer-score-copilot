"""Linux stopped-store backup; same OS lease as src.api; stdlib only.

Run as root after stopping BOTH cloud containers. No restore into a live store.
"""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or destination.exists() or source == destination:
        raise ValueError('Source must exist and destination must be a new path')
    if not destination.parent.is_dir():
        raise ValueError('Destination directory must exist')
    # Fail while the API owns its process lease. Release also on exceptions.
    with Path(str(source) + '.lock').open('a+b') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                destination.chmod(0o600)
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Backup integrity check failed')
                tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                required = {'review_cases', 'api_requests', 'checkpoints'}
                if not required <= tables:
                    raise ValueError('Missing case/idempotency/LangGraph checkpoint tables')
                counts = {t: dst.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                          for t in sorted(required)}
    result = dict(sha256=hashlib.sha256(destination.read_bytes()).hexdigest(), counts=counts)
    receipt = destination.with_suffix(destination.suffix + '.json')
    with receipt.open('x', encoding='utf-8') as out:
        receipt.chmod(0o600)
        json.dump(result, out, indent=2)
        out.write('\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(backup(args.source, args.destination), indent=2))
