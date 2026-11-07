"""Durable offline application boundary shared by HTTP and the single worker.

Uses real frozen account rows, not the fixed M1 teaching evidence. No SDK calls.
"""
from __future__ import annotations

from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import threading

from src import batch_scan
from src.review_workflow import CaseStore, TransitionError, digest, new_case
from src.review_graph import (GRAPH_VERSION, config, open_graph, resume_case,
                              submit_decision)

CONTRACT = 'frozen-account-scan-v1'
CODE_FILES = ('batch_scan.py', 'policy_decisions.py', 'temporal_policy.py',
              'case_application.py', 'review_workflow.py', 'review_graph.py')


class MissingCase(LookupError):
    pass


def implementation_hashes():
    root = Path(__file__).parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in CODE_FILES}


def capture(request, scores_path, source_root):
    """Only snapshot I/O in submission; analysis is performed by the worker."""
    rows, invalid_date, exists = batch_scan._snapshot(scores_path, request)[request['account_ids'][0]]
    if len(rows) > 1200:
        raise ValueError('History exceeds the local case limit')
    policy_bytes = {p.name: p.read_bytes().hex()
                    for p in sorted((Path(source_root) / 'policy_memos').glob('*.md'))}
    if (len(policy_bytes) != 7 or {name.split('_')[0] for name in policy_bytes} !=
            {f'POL-{i:03}' for i in range(1, 8)}):
        raise ValueError('Expected the seven baseline policies')
    if sum(len(body) // 2 for body in policy_bytes.values()) > 131072:
        raise ValueError('Policy snapshot exceeds the local case limit')
    for body in policy_bytes.values():
        bytes.fromhex(body).decode('utf-8')
    evidence = dict(request=request, rows=rows, invalid_date=invalid_date,
                    account_exists=exists, policy_bytes=policy_bytes)
    provenance = dict(contract=CONTRACT, implementation=implementation_hashes(),
                      policy_sha256={name: hashlib.sha256(bytes.fromhex(body)).hexdigest()
                                     for name, body in policy_bytes.items()},
                      policy_version='v1.0', retrieval='not_requested', live_notes=False)
    # Reject non-serializable / NaN inputs before insertion.
    digest([evidence, provenance])
    return evidence, provenance


def analyze_frozen(evidence):
    request = evidence['request']
    result = batch_scan._base(request['account_ids'][0], request['as_of_period'])
    if not evidence['account_exists']:
        result.update(scan_outcome='insufficient_data', reason_codes=['ACCOUNT_NOT_FOUND'],
                      next_action='verify_account_id')
    else:
        batch_scan._classify(result, copy.deepcopy(evidence['rows']), evidence['invalid_date'])
    # 'passed' refers to this deterministic package, never to an LLM note or
    # authorization to change credit conditions. Missing/unsupported data hold.
    verification = ('passed' if result['scan_outcome'] in
                    ('attention_required', 'no_signal_in_scope') else 'incomplete')
    return dict(claims=[], answer=None, scan=result,
                scope='Deterministic market-1 scan; no generated note; review package only'), verification


def safe_held_scan(analysis, verification):
    """Only used with the deterministic analyzer; never accept model drafts here."""
    return dict(analysis, reason_codes=['VERIFICATION_' + verification.upper()])


class ApplicationStore(CaseStore):
    def __init__(self, path):
        super().__init__(path)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS api_requests '
                       '(request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, '
                       'case_id TEXT UNIQUE NOT NULL)')

    def lookup(self, request_key, request_hash):
        with closing(self.connect()) as db:
            row = db.execute('SELECT request_hash, case_id FROM api_requests WHERE request_key=?',
                             (request_key,)).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise TransitionError('Idempotency key already used for another request')
        return self.read(row[1])

    def submit(self, request_key, request_hash, evidence, provenance):
        case = new_case(evidence, provenance)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT request_hash, case_id FROM api_requests WHERE request_key=?',
                             (request_key,)).fetchone()
            if row:
                if row[0] != request_hash:
                    raise TransitionError('Idempotency key already used for another request')
                return self.read(row[1])
            db.execute('INSERT INTO review_cases VALUES (?, ?, ?)',
                       (case['case_id'], 0, json.dumps(case, allow_nan=False)))
            db.execute('INSERT INTO api_requests VALUES (?, ?, ?)',
                       (request_key, request_hash, case['case_id']))
        return case

    def ids(self, limit=50, offset=0):
        with closing(self.connect()) as db:
            return [r[0] for r in db.execute(
                'SELECT case_id FROM api_requests ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit, offset))]

    def pending_ids(self):
        # Query only API-owned cases. M1 teaching cases cannot enter this worker.
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT r.case_id FROM api_requests r JOIN review_cases c ON c.case_id=r.case_id "
                "WHERE json_extract(c.body, '$.workflow_status') IN "
                "('queued', 'running', 'completed', 'awaiting_review') ORDER BY r.rowid").fetchall()
        return [row[0] for row in rows]

    def read_owned(self, case_id):
        with closing(self.connect()) as db:
            if db.execute('SELECT 1 FROM api_requests WHERE case_id=?', (case_id,)).fetchone() is None:
                raise MissingCase()
        return self.read(case_id)


def projection(case, *, detail=True):
    view = {k: case[k] for k in ('case_id', 'revision', 'workflow_status', 'verification_status',
                                'analyst_decision', 'package_hash', 'snapshot_hash',
                                'created_at', 'updated_at')}
    view.update(case['evidence']['request'])
    if detail:
        view.update(analysis=case['analysis'], provenance=case['provenance'],
                    evidence=case['evidence']['rows'],
                    policy_memos=[dict(source_file=name, text=bytes.fromhex(body).decode('utf-8'),
                                       sha256=case['provenance']['policy_sha256'][name])
                                  for name, body in case['evidence']['policy_bytes'].items()],
                    limitations=['Offline deterministic scan only; live notes disabled.',
                                 'Approval accepts this package only; it authorizes no credit action.'])
        view['reason_codes'] = [e['reason_code'] for e in case['events'] if e.get('reason_code')]
    return view


class CaseApplication:
    def __init__(self, store_path, scores_path, source_root):
        self.store = ApplicationStore(store_path)
        self.scores_path, self.source_root = Path(scores_path), Path(source_root)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.failure = False
        self.thread = None
        self.processed = {}

    def submit(self, request, key):
        request_hash = digest(request)
        # Idempotent replay never rereads changed input files.
        existing = self.store.lookup(key, request_hash)
        if existing is not None:
            return existing
        evidence, provenance = capture(request, self.scores_path, self.source_root)
        return self.store.submit(key, request_hash, evidence, provenance)

    def tick(self):
        with self.lock:
            for case_id in self.store.pending_ids():
                case = self.store.read_owned(case_id)
                if self.processed.get(case_id) == case['revision']:
                    continue
                if case['provenance']['implementation'] != implementation_hashes():
                    if case['workflow_status'] in ('queued', 'running'):
                        if case['workflow_status'] == 'queued':
                            case = self.store.transition(case_id, case['revision'], 'running')
                        self.store.transition(case_id, case['revision'], 'held',
                                              reason_code='IMPLEMENTATION_CHANGED')
                    continue
                with open_graph(self.store.path, analyzer=analyze_frozen,
                                held_projection=safe_held_scan) as (store, graph):
                    state = graph.get_state(config(case_id))
                    if not state.values:
                        graph.invoke(dict(case_id=case_id, snapshot_hash=case['snapshot_hash'],
                                          graph_version=GRAPH_VERSION), config(case_id), durability='sync')
                    view = resume_case(store, graph, case_id)
                    self.processed[case_id] = view['case']['revision']

    def decide(self, case_id, payload):
        with self.lock:
            case = self.store.read_owned(case_id)
            if case['provenance']['implementation'] != implementation_hashes():
                raise TransitionError('Implementation changed; create a new case')
            with open_graph(self.store.path, analyzer=analyze_frozen,
                            held_projection=safe_held_scan) as (store, graph):
                return submit_decision(store, graph, case_id, payload)['case']

    def run(self):
        try:
            while not self.stop.is_set():
                self.tick()
                self.stop.wait(0.25)
        except Exception:
            # Fail readiness, preserve saved inputs, and do not spin or replay.
            self.failure = True

    def start(self):
        self.thread = threading.Thread(target=self.run, name='case-worker', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()  # Never release the process lease while work continues.
