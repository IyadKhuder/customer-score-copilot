"""Bounded offline scans. No provider imports or changes to policy semantics."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.policy_decisions import evaluate_policy_decision

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ('failed', 'unsupported', 'insufficient_data', 'attention_required', 'no_signal_in_scope')
POLICIES = ('POL-002', 'POL-004', 'POL-005', 'POL-007')


class InvalidScanRequest(ValueError):
    """INVALID_REQUEST: reject the entire request before accessing SQLite."""


def validate_request(request):
    if (type(request) is not dict or set(request) !=
            {'schema_version', 'account_ids', 'market', 'as_of_period'}):
        raise InvalidScanRequest('INVALID_REQUEST: unexpected or missing fields')
    ids = request['account_ids']
    if (type(request['schema_version']) is not int or request['schema_version'] != 1
            or type(request['market']) is not int or request['market'] != 1
            or type(ids) is not list or not 1 <= len(ids) <= 25
            or any(type(i) is not int or not 1 <= i <= 2**63-1 for i in ids)
            or len(set(ids)) != len(ids)):
        raise InvalidScanRequest('INVALID_REQUEST: require market 1 and 1–25 unique SQLite account IDs')
    try:
        period = request['as_of_period']
        parsed = date.fromisoformat(period)
        if parsed.isoformat() != period or parsed.day != 1 or parsed.year < 2:
            raise ValueError()
    except (TypeError, ValueError):
        raise InvalidScanRequest('INVALID_REQUEST: require YYYY-MM-01 (year >= 0002)') from None
    return dict(request, account_ids=sorted(ids))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _sha(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _period(value):
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Invalid period')
    return value[:7]


def _snapshot(db_path, request):
    """One read transaction; no implicit database creation or per-account connections."""
    uri = Path(db_path).resolve().as_uri() + '?mode=ro'
    snapshots = {}
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.execute('PRAGMA query_only = ON')
        conn.execute('BEGIN')
        for account in request['account_ids']:
            rows = conn.execute(
                'SELECT period, score, bin, market FROM scores WHERE account_id = ? ORDER BY period',
                (account,),
            ).fetchall()
            selected = []
            invalid_date = False
            for row in rows:
                try:
                    month = _period(row[0])
                except (ValueError, TypeError):
                    invalid_date = True
                    continue
                if month <= request['as_of_period'][:7]:
                    selected.append(dict(zip(('period', 'score', 'bin', 'market'), row)))
            snapshots[account] = (selected, invalid_date)
    return snapshots


def _source(root):
    source = {'git_commit': None, 'tracked_dirty': None, 'policy_sha256': {}}
    try:
        source['git_commit'] = subprocess.check_output(
            ['git', '-C', str(root), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL,
            text=True, timeout=5).strip()
        source['tracked_dirty'] = bool(subprocess.check_output(
            ['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'],
            stderr=subprocess.DEVNULL, text=True, timeout=5).strip())
    except (OSError, subprocess.SubprocessError):
        pass
    for policy in POLICIES:
        matches = list((root / 'policy_memos').glob(policy + '_*.md'))
        if len(matches) != 1:
            raise ValueError('Policy source missing or ambiguous')
        source['policy_sha256'][policy] = hashlib.sha256(matches[0].read_bytes()).hexdigest()
    # Also record new, potentially untracked scanner bytes, not merely git HEAD.
    source['scanner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return source


def _base(account, period):
    return dict(account_id=account, market=None, as_of_period=period,
                scan_outcome=None, reason_codes=[], next_action='none',
                coverage_complete=None, missing_baseline_months=[], scan_evidence=[],
                policy_decision=None, error=None,
                narrative=dict(status='not_requested', publishable=False, answer=None,
                               reason_codes=[], next_action='none'))


def _classify(result, rows, invalid_date):
    if invalid_date:
        raise ValueError('Invalid date')
    seen = set()
    markets = set()
    for row in rows:
        month = _period(row['period'])
        if (month in seen or type(row['score']) is not int or not 0 <= row['score'] <= 1000
                or type(row['bin']) is not int or not 1 <= row['bin'] <= 9
                or type(row['market']) is not int):
            raise ValueError('Invalid history')
        seen.add(month)
        markets.add(row['market'])
    if len(markets) > 1:
        raise ValueError('Mixed markets')
    result['scan_evidence'] = rows
    result['market'] = next(iter(markets), None)
    if markets and markets != {1}:
        result.update(scan_outcome='unsupported', reason_codes=['UNSUPPORTED_MARKET'],
                      next_action='use_supported_market')
        return
    missing_code = ('NO_HISTORY_AS_OF' if not rows else
                    'MISSING_AS_OF_OBSERVATION' if result['as_of_period'][:7] not in seen else None)
    if missing_code:
        result.update(scan_outcome='insufficient_data', reason_codes=[missing_code],
                      next_action='gather_evidence')
        return
    decision = evaluate_policy_decision(rows, result['as_of_period'])
    result.update(policy_decision=decision, coverage_complete=decision['coverage_complete'],
                  missing_baseline_months=decision['missing_baseline_months'])
    conditions = [
        (decision['data_quality_review'] == 'required', 'DATA_QUALITY_REVIEW_REQUIRED', 'data_quality_review'),
        (decision['committee_review_account_rules'] == 'indicated', 'COMMITTEE_REVIEW_INDICATED', 'committee_review'),
        (decision['analyst_referral'] == 'indicated', 'ANALYST_REFERRAL_INDICATED', 'analyst_review'),
        (decision['warning_signal_status'] == 'triggered', 'WARNING_SIGNAL_TRIGGERED', 'review_warning_signal'),
        (any(e['review_state'] == 'pending_data_quality' for e in decision['events'])
         or any(e['review_state'] == 'pending_data_quality' for e in decision['severe_downward_migrations'])
         or 'pending_data_quality' in (decision['analyst_referral'], decision['committee_review_account_rules']),
         'SIGNAL_PENDING_DATA_QUALITY', 'data_quality_review'),
    ]
    hits = [(code, action) for condition, code, action in conditions if condition]
    result['reason_codes'] = [code for code, _ in hits]
    if not decision['coverage_complete']:
        result['reason_codes'].append('INCOMPLETE_BASELINE')
    if hits:
        result.update(scan_outcome='attention_required', next_action=hits[0][1])
    elif not decision['coverage_complete']:
        result.update(scan_outcome='insufficient_data', next_action='gather_evidence')
    else:
        result['scan_outcome'] = 'no_signal_in_scope'


def scan_batch(request, db_path='data/scores.db', *, source_root=ROOT):
    """Return a JSON-compatible report; isolate account errors, retain run failures."""
    request = validate_request(request)
    report = dict(schema_version=1, run_id=str(uuid4()),
                  created_at_utc=datetime.now(timezone.utc).isoformat(), request=request,
                  source={}, scope=dict(market=1, policies=list(POLICIES),
                      excluded=['other markets', 'POL-001 severe upward review', 'POL-003 actions',
                                'POL-006 recovery', 'site aggregate rules', 'narratives']),
                  run_status='failed', run_error=None, results=[])
    try:
        report['source'] = _source(Path(source_root))
        snapshots = _snapshot(db_path, request)
        # SQLite values can include blobs/non-finite floats. Never hash repr() or leak raw errors.
        safe = []
        for account, (rows, invalid_date) in snapshots.items():
            try:
                encoded = _canonical(rows)
            except (ValueError, TypeError):
                encoded = None
            safe.append(dict(account_id=account, history_json=encoded, invalid_date=invalid_date))
        report['source']['input_sha256'] = _sha(dict(request=request, snapshot=safe))
    except Exception as exc:
        report['run_error'] = dict(code='SNAPSHOT_OR_SOURCE_FAILED', error_type=type(exc).__name__)
    else:
        for account, (rows, invalid_date) in snapshots.items():
            result = _base(account, request['as_of_period'])
            try:
                _classify(result, rows, invalid_date)
            except Exception as exc:
                code = 'INVALID_HISTORY' if isinstance(exc, (ValueError, TypeError, KeyError)) else 'ACCOUNT_SCAN_FAILED'
                result.update(scan_outcome='failed', reason_codes=[code],
                              next_action='repair_history' if code == 'INVALID_HISTORY' else 'inspect_error',
                              error=dict(code=code, error_type=type(exc).__name__))
            report['results'].append(result)
        report['run_status'] = 'completed_with_errors' if any(
            r['scan_outcome'] == 'failed' for r in report['results']) else 'completed'
    counts = {outcome: sum(r['scan_outcome'] == outcome for r in report['results']) for outcome in OUTCOMES}
    report['summary'] = dict(requested_count=len(request['account_ids']),
                             result_count=len(report['results']), outcomes=counts)
    return report


def save_scan_report(report, destination):
    """Exclusive creation; caller owns destination directory and prior reports."""
    payload = json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n'
    with Path(destination).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(payload)
    return Path(destination)
