"""Isolated offline case-review HTTP/worker benchmark. No Azure or provider calls."""
import argparse
from contextlib import closing
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter, sleep

from fastapi.testclient import TestClient
from src.api import create_app
from src.case_application import implementation_hashes
from src.operations import distribution, utcnow

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'benchmark-disposable-offline-only-token'


def run_benchmark(*, repetitions=5):
    if type(repetitions) is not int or not 1 <= repetitions <= 30:
        raise ValueError('Require 1–30 repetitions')
    rows = []
    with TemporaryDirectory(prefix='case-review-benchmark-') as directory:
        root = Path(directory)
        scores = root / 'scores.db'
        # Authored synthetic histories, independent of the owner's selected DB.
        with closing(sqlite3.connect(scores)) as db, db:
            db.execute('CREATE TABLE scores (account_id INTEGER, period TEXT, score INTEGER, bin INTEGER, market INTEGER)')
            db.executemany('INSERT INTO scores VALUES (?,?,?,?,?)',
                [(1000003, f'1991-{m:02}-01', s, 1 + s // 50, 1)
                 for m, s in [(9, 350), (10, 320), (11, 300), (12, 265)]])
        app = create_app(store_path=root / 'cases.db', scores_path=scores, token=TOKEN)
        with TestClient(app) as client:
            # One warmup per scenario; exclude warmups from report distributions.
            for repetition in range(-1, repetitions):
                for scenario, account, expected in [('declining', 1000003, 'awaiting_review'),
                                                     ('absent_account', 9999999, 'held')]:
                    headers = {'Authorization': 'Bearer ' + TOKEN,
                               'Idempotency-Key': f'{scenario}-{repetition}'}
                    started = perf_counter()
                    response = client.post('/cases', headers=headers,
                        json=dict(account_id=account, market=1, as_of_period='1991-12-01'))
                    submission_ms = (perf_counter() - started) * 1000
                    if response.status_code != 202:
                        raise RuntimeError('Benchmark submission failed')
                    location = response.headers['Location']
                    deadline = perf_counter() + 15
                    while True:
                        result = client.get(location, headers=headers)
                        if result.status_code != 200:
                            raise RuntimeError('Benchmark read failed')
                        case = result.json()
                        if case['workflow_status'] not in {'queued', 'running'}:
                            break
                        if perf_counter() >= deadline:
                            raise RuntimeError('Benchmark worker deadline exceeded')
                        sleep(.01)
                    elapsed_ms = (perf_counter() - started) * 1000
                    expected_verification = 'passed' if expected == 'awaiting_review' else 'incomplete'
                    passed = (case['workflow_status'] == expected and
                              case['verification_status'] == expected_verification and
                              case['package_hash'] is not None)
                    if repetition >= 0:
                        rows.append(dict(scenario=scenario, repetition=repetition,
                            workflow_status=case['workflow_status'],
                            verification_status=case['verification_status'],
                            passed=passed, submission_ms=submission_ms, end_to_end_ms=elapsed_ms))
                    if not passed:
                        raise RuntimeError('Unexpected benchmark package outcome')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    files = ['src/api.py', 'src/operations.py', 'src/measured_application.py',
             'scripts/benchmark_cases_phase2.py']
    fingerprints = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files}
    policies = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((ROOT / 'policy_memos').glob('*.md'))}
    return dict(benchmark='offline-case-review-v1', recorded_at=utcnow().isoformat(),
        revision=revision, working_tree_dirty=dirty, python=platform.python_version(),
        platform=platform.platform(), dependencies={name: version(name) for name in
            ('fastapi', 'langgraph', 'langgraph-checkpoint-sqlite', 'httpx')},
        implementation=implementation_hashes(),
        benchmark_code_sha256=fingerprints, policy_sha256=policies,
        repetitions=repetitions, warmups_excluded=2, samples=len(rows), passed=sum(r['passed'] for r in rows),
        scope='Sequential in-process TestClient HTTP -> real background worker -> readback. '
              'Includes startup/lazy effects after warmup, 10ms polling interval plus scheduling/read overhead, SQLite and graph writes; '
              'excludes network/TLS, browser, analyst waiting, decisions and Azure. '
              'No throughput, concurrency, live-model quality or production-scale claim.',
        summaries={scenario: {metric: distribution([r[metric] for r in rows if r['scenario'] == scenario])
                   for metric in ('submission_ms', 'end_to_end_ms')}
                   for scenario in ('declining', 'absent_account')}, rows=rows,
        provider=dict(token_usage=None, monetary_cost=None, reason='Offline; no provider calls.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--repetitions', type=int, default=5)
    args = parser.parse_args()
    # Refuse existing destinations before doing work; exclusive creation at write.
    if args.output.exists():
        parser.error('Output already exists; choose a new filename')
    report = run_benchmark(repetitions=args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(f"Offline case-review benchmark: {report['passed']}/{report['samples']} expected outcomes")


if __name__ == '__main__':
    main()
