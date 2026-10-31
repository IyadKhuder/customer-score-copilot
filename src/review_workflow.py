"""Offline workflow teaching skeleton; independent of Slack and provider SDKs."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class TransitionError(ValueError):
    """The requested operation conflicts with the saved case state."""


class CaseStore:
    """Single-worker local demo. Each state update uses a revision comparison."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS review_cases '
                       '(case_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path)

    def create(self):
        evidence = {'account_id': 'workflow-demo-001', 'market': 'synthetic-demo',
                    'periods': ['1991-10', '1991-11', '1991-12'], 'scores': [350, 320, 300]}
        provenance = {'policy': 'workflow-demo-only-v1',
                      'config': 'deterministic-endpoint-difference-v1',
                      'code_contract': 'workflow-skeleton-v1'}
        case = dict(schema_version=1, case_id=str(uuid4()), revision=0,
                    workflow_status='queued', verification_status='not_requested',
                    analyst_decision='pending', evidence=evidence, provenance=provenance,
                    snapshot_hash=digest([evidence, provenance]), analysis=None,
                    package_hash=None, created_at=now(), updated_at=now(), events=[])
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO review_cases VALUES (?, ?, ?)',
                       (case['case_id'], 0, json.dumps(case)))
        return case

    def read(self, case_id):
        with closing(self.connect()) as db:
            row = db.execute('SELECT body FROM review_cases WHERE case_id=?', (case_id,)).fetchone()
        if row is None:
            raise TransitionError('Unknown case ID')
        case = json.loads(row[0])
        if case['schema_version'] != 1:
            raise TransitionError('Unsupported case schema')
        if case['snapshot_hash'] != digest([case['evidence'], case['provenance']]):
            raise TransitionError('Snapshot integrity mismatch')
        if case['analysis'] is not None and case['package_hash'] != digest(
                [case['snapshot_hash'], case['analysis'], case['verification_status']]):
            raise TransitionError('Review package integrity mismatch')
        return case

    def transition(self, case_id, expected_revision, target, *, analysis=None, verification=None):
        case = self.read(case_id)
        if case['revision'] != expected_revision:
            raise TransitionError('Stale revision')
        current = case['workflow_status']
        if (current, target) not in {('queued', 'running'), ('running', 'awaiting_review'),
                                    ('running', 'held'), ('running', 'failed')}:
            raise TransitionError(f'Invalid transition: {current} -> {target}')
        if target == 'awaiting_review' and (verification != 'passed' or analysis is None):
            raise TransitionError('Awaiting review requires a passed, saved analysis')
        if analysis is not None:
            if verification not in {'passed', 'blocked', 'incomplete'}:
                raise TransitionError('Analysis requires an explicit verification result')
            if target not in {'awaiting_review', 'held'}:
                raise TransitionError('Cannot save analysis in this state')
            if target == 'held' and verification == 'passed':
                raise TransitionError('Passed analysis must await review')
            case.update(analysis=analysis, verification_status=verification)
            case['package_hash'] = digest([case['snapshot_hash'], analysis, verification])
        case['events'].append(dict(from_status=current, to_status=target, at=now()))
        case.update(workflow_status=target, revision=expected_revision + 1, updated_at=now())
        with closing(self.connect()) as db, db:
            result = db.execute('UPDATE review_cases SET revision=?, body=? '
                                'WHERE case_id=? AND revision=?',
                                (case['revision'], json.dumps(case), case_id, expected_revision))
            if result.rowcount != 1:
                raise TransitionError('Concurrent update; reload the case')
        return case


def analyze_demo(evidence):
    """One analysis node: structured claim, deterministic check, then composition.

    This is deliberately NOT a substitute for the project's policy verifier.
    """
    scores = evidence['scores']
    claim = {'kind': 'endpoint_difference', 'value': scores[-1] - scores[0]}
    verified = claim['value'] == evidence['scores'][-1] - evidence['scores'][0]
    return {'claims': [claim], 'answer': f"Recorded endpoint score change: {claim['value']}."}, (
        'passed' if verified else 'blocked')


def resume(store, case_id, analyzer=analyze_demo):
    case = store.read(case_id)
    if case['workflow_status'] == 'running':
        # An interrupted attempt has no recorded output: never silently replay it.
        return store.transition(case_id, case['revision'], 'held')
    if case['workflow_status'] != 'queued':
        return case  # Saved analysis remains unchanged; no analyzer call.
    case = store.transition(case_id, case['revision'], 'running')
    try:
        analysis, verification = analyzer(case['evidence'])
    except Exception:
        return store.transition(case_id, case['revision'], 'failed')
    return store.transition(case_id, case['revision'],
                            'awaiting_review' if verification == 'passed' else 'held',
                            analysis=analysis, verification=verification)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True, help='Separate local workflow SQLite file')
    parser.add_argument('command', choices=['create', 'show', 'resume'])
    parser.add_argument('case_id', nargs='?')
    args = parser.parse_args()
    if args.command != 'create' and not args.case_id:
        parser.error('show/resume requires case_id')
    store = CaseStore(args.store)
    try:
        case = store.create() if args.command == 'create' else (
            store.read(args.case_id) if args.command == 'show' else resume(store, args.case_id))
    except (TransitionError, sqlite3.Error) as exc:
        parser.exit(2, f'Workflow error: {exc}\n')
    print(json.dumps(case, indent=2))


if __name__ == '__main__':
    main()
