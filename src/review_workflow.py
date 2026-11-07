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

DEMO_REVIEWERS = frozenset({'demo-analyst'})


def validate_decision(case, payload, allowed_reviewers=DEMO_REVIEWERS):
    """Validate before graph execution and again before the atomic case write.

    Reviewer names are operator-declared in this offline CLI, not authenticated.
    """
    fields = {'case_id', 'expected_revision', 'package_hash', 'decision', 'reviewer', 'reason'}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise TransitionError('Decision requires exactly the documented fields')
    if type(payload['expected_revision']) is not int or payload['expected_revision'] < 0:
        raise TransitionError('Expected revision must be a nonnegative integer')
    for field, limit in [('case_id', 80), ('package_hash', 64), ('reviewer', 80), ('reason', 1000)]:
        value = payload[field]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise TransitionError(f'Invalid {field}')
    if payload['reviewer'] not in allowed_reviewers:
        raise TransitionError('Reviewer is not permitted')
    if payload['decision'] not in ('approved', 'rejected'):
        raise TransitionError('Decision must be approved or rejected')
    if payload['case_id'] != case['case_id']:
        raise TransitionError('Decision case ID mismatch')
    if case['analyst_decision'] != 'pending' or case.get('decision') is not None:
        raise TransitionError('A decision has already been recorded')
    if case['workflow_status'] != 'awaiting_review':
        raise TransitionError('Case is not awaiting review')
    if case['verification_status'] != 'passed' or case['analysis'] is None:
        raise TransitionError('Decision requires passed verification and a saved package')
    if payload['expected_revision'] != case['revision']:
        raise TransitionError('Stale revision; reload the review package')
    if payload['package_hash'] != case['package_hash']:
        raise TransitionError('Review package hash mismatch')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class TransitionError(ValueError):
    """The requested operation conflicts with the saved case state."""


def new_case(evidence, provenance):
    """Construct one immutable-input case; callers persist it atomically."""
    case = dict(schema_version=1, case_id=str(uuid4()), revision=0,
                workflow_status='queued', verification_status='not_requested',
                analyst_decision='pending', evidence=evidence, provenance=provenance,
                snapshot_hash=digest([evidence, provenance]), analysis=None,
                package_hash=None, decision=None, decision_hash=None,
                created_at=now(), updated_at=now(), events=[])
    return case


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
        case = new_case(evidence, provenance)
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO review_cases VALUES (?, ?, ?)',
                       (case['case_id'], 0, json.dumps(case)))
        return case

    def read(self, case_id):
        with closing(self.connect()) as db:
            row = db.execute('SELECT revision, body FROM review_cases WHERE case_id=?', (case_id,)).fetchone()
        if row is None:
            raise TransitionError('Unknown case ID')
        case = json.loads(row[1])
        if case['case_id'] != case_id or case['revision'] != row[0]:
            raise TransitionError('Case identity or revision mismatch')
        if case['schema_version'] != 1:
            raise TransitionError('Unsupported case schema')
        if case['snapshot_hash'] != digest([case['evidence'], case['provenance']]):
            raise TransitionError('Snapshot integrity mismatch')
        if case['analysis'] is not None and case['package_hash'] != digest(
                [case['snapshot_hash'], case['analysis'], case['verification_status']]):
            raise TransitionError('Review package integrity mismatch')
        decision = case.get('decision')
        if decision is not None:
            fields = {'decision', 'reviewer', 'reason', 'decided_at', 'reviewed_revision',
                      'package_hash', 'snapshot_hash'}
            if (not isinstance(decision, dict) or set(decision) != fields
                    or case.get('decision_hash') != digest(decision)
                    or decision['package_hash'] != case['package_hash']
                    or decision['snapshot_hash'] != case['snapshot_hash']
                    or decision['reviewed_revision'] != case['revision'] - 1
                    or decision['decision'] != case['analyst_decision']
                    or case['workflow_status'] != 'completed'
                    or case['verification_status'] != 'passed'):
                raise TransitionError('Decision integrity mismatch')
        elif case['analyst_decision'] != 'pending' or case['workflow_status'] == 'completed':
            raise TransitionError('Completed case requires a recorded decision')
        return case

    def transition(self, case_id, expected_revision, target, *, analysis=None, verification=None,
                   reason_code=None):
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
        event = dict(from_status=current, to_status=target, at=now())
        if reason_code is not None:
            event['reason_code'] = reason_code
        case['events'].append(event)
        case.update(workflow_status=target, revision=expected_revision + 1, updated_at=now())
        with closing(self.connect()) as db, db:
            result = db.execute('UPDATE review_cases SET revision=?, body=? '
                                'WHERE case_id=? AND revision=?',
                                (case['revision'], json.dumps(case), case_id, expected_revision))
            if result.rowcount != 1:
                raise TransitionError('Concurrent update; reload the case')
        return case

    def decide(self, case_id, payload, allowed_reviewers=DEMO_REVIEWERS):
        case = self.read(case_id)
        validate_decision(case, payload, allowed_reviewers)
        revision = case['revision']
        recorded_at = now()
        decision = dict(decision=payload['decision'], reviewer=payload['reviewer'],
                        reason=payload['reason'].strip(), decided_at=recorded_at,
                        reviewed_revision=revision, package_hash=case['package_hash'],
                        snapshot_hash=case['snapshot_hash'])
        case.update(workflow_status='completed', analyst_decision=payload['decision'],
                    decision=decision, decision_hash=digest(decision),
                    revision=revision + 1, updated_at=recorded_at)
        case['events'].append(dict(from_status='awaiting_review', to_status='completed',
                                   at=recorded_at, decision=payload['decision']))
        # Decision, status and audit event commit together. A competing stale
        # writer cannot overwrite the first decision after passing validation.
        with closing(self.connect()) as db, db:
            result = db.execute('UPDATE review_cases SET revision=?, body=? '
                                'WHERE case_id=? AND revision=?',
                                (case['revision'], json.dumps(case), case_id, revision))
            if result.rowcount != 1:
                raise TransitionError('Concurrent decision; reload the case')
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


def resume(store, case_id, analyzer=analyze_demo, *, held_projection=None):
    case = store.read(case_id)
    if case['workflow_status'] == 'running':
        # An interrupted attempt has no recorded output: never silently replay it.
        return store.transition(case_id, case['revision'], 'held',
                                reason_code='INTERRUPTED_ANALYSIS_NO_RESULT')
    if case['workflow_status'] != 'queued':
        return case  # Saved analysis remains unchanged; no analyzer call.
    case = store.transition(case_id, case['revision'], 'running')
    try:
        analysis, verification = analyzer(case['evidence'])
    except Exception:
        return store.transition(case_id, case['revision'], 'failed',
                                reason_code='ANALYSIS_EXECUTION_FAILED')
    if not isinstance(verification, str) or verification not in {'passed', 'blocked', 'incomplete'}:
        return store.transition(case_id, case['revision'], 'failed',
                                reason_code='INVALID_VERIFICATION_RESULT')
    if verification != 'passed':
        # Keep frozen evidence and a safe diagnostic, not unverified draft text.
        analysis = (held_projection(analysis, verification) if held_projection else
                    {'claims': [], 'answer': None,
                     'reason_codes': ['VERIFICATION_' + verification.upper()]})
    return store.transition(case_id, case['revision'],
                            'awaiting_review' if verification == 'passed' else 'held',
                            analysis=analysis, verification=verification,
                            reason_code=None if verification == 'passed' else
                            'VERIFICATION_' + verification.upper())


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
