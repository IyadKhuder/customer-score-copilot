"""Slack-independent single-account review service. No network calls on import."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from threading import Lock
from time import monotonic, sleep
from uuid import uuid4

from src.batch_scan import ROOT, scan_batch, validate_request
from src.request_budget import RequestBudget
from src.slack_summary import describe_scan

USAGE = 'Use: review account 1000008 as of 1991-12 (market 1 only). Each request is independent.'


def parse_request(text):
    """Parse text after transport removes this bot's leading mention."""
    if not isinstance(text, str):
        raise ValueError(USAGE)
    if text.strip().lower() == 'help':
        return None
    match = re.fullmatch(r'\s*review\s+account\s+([0-9]{1,19})\s+as\s+of\s+([0-9]{4})-([0-9]{2})\s*', text, re.I)
    if not match:
        raise ValueError(USAGE)
    account, year, month = map(int, match.groups())
    try:
        period = date(year, month, 1).isoformat()
        return validate_request(dict(schema_version=1, account_ids=[account], market=1, as_of_period=period))
    except ValueError:
        raise ValueError(USAGE) from None


class ReviewService:
    """Own a serial session allowance; client_factory returns (fresh adapter, kind).

    Call review with command text only. Returned dictionaries contain a public
    projection, never tool evidence, private audits or raw exception messages.
    Session allowance is operator-declared, not provider-wide usage.
    """
    def __init__(self, db_path, *, source_root=ROOT, client_factory=None,
                 daily_remaining=0, session_max_requests=8, per_review_max_requests=8,
                 interval_seconds=5.0, clock=monotonic, sleeper=sleep, log_dir=None):
        # Reuse the existing budget's validation without starting a request.
        check = RequestBudget(daily_remaining=daily_remaining, max_requests=session_max_requests,
                              interval_seconds=interval_seconds, clock=clock, sleeper=sleeper)
        if type(per_review_max_requests) is not int or not 1 <= per_review_max_requests <= 8:
            raise ValueError('per_review_max_requests must be 1..8')
        if client_factory is not None and not callable(client_factory):
            raise ValueError('client_factory must be callable')
        self.db_path = Path(db_path).resolve()
        self.root = Path(source_root).resolve()
        self.factory = client_factory
        self.log_dir = Path(log_dir).resolve() if log_dir is not None else None
        if self.log_dir is not None and not self.log_dir.is_dir():
            raise ValueError("Request log directory must already exist")
        self.remaining = check.limit
        self.per_review = per_review_max_requests
        self.interval, self.clock, self.sleeper = interval_seconds, clock, sleeper
        self.last_start = None
        self.lock = Lock()

    def review(self, text):
        try:
            request = parse_request(text)
        except ValueError:
            return dict(status='invalid_request', message=USAGE)
        if request is None:
            return dict(status='help', message=USAGE)
        if not self.lock.acquire(blocking=False):
            return dict(status='busy', message='A review is running. Try again after it finishes.')
        try:
            return self._review(request)
        finally:
            self.lock.release()

    def _review(self, request):
        report = scan_batch(request, self.db_path, source_root=self.root)
        context = dict(account_id=request['account_ids'][0], market=1,
                       as_of_period=request['as_of_period'])
        if not report['results']:
            return dict(context, status='failed', reason_codes=['SNAPSHOT_OR_SOURCE_FAILED'],
                        next_action='inspect_local_sources')
        result = report['results'][0]
        public = dict(context, status=result['scan_outcome'],
                      reason_codes=list(result['reason_codes']), next_action=result['next_action'],
                      coverage_complete=result['coverage_complete'],
                      missing_baseline_months=list(result['missing_baseline_months']),
                      score_summary=describe_scan(result),
                      note=dict(status='not_requested', publishable=False, answer=None,
                                reason_codes=[], next_action='none'))
        if result['scan_outcome'] != 'attention_required':
            return public
        note = public['note']
        if self.factory is None or not self.remaining:
            note.update(reason_codes=['LIVE_NOTES_DISABLED' if self.factory is None else 'REQUEST_BUDGET_EXHAUSTED'])
            return public
        budget = RequestBudget(daily_remaining=self.remaining, max_requests=self.per_review,
                               interval_seconds=self.interval, clock=self.clock, sleeper=self.sleeper)
        # Fresh budget, shared pacing boundary: no allowance reset between reviews.
        budget.last = self.last_start
        client = None
        original_sdk = None
        try:
            from src.batch_notes import add_verified_notes
            if self.log_dir is not None:
                budget.attach_log(self.log_dir / (str(uuid4()) + '.requests.jsonl'))
            client, kind = self.factory()
            original_sdk = client.client
            enriched = add_verified_notes(report, client, budget, adapter_kind=kind,
                                          max_notes=1, max_tool_rounds=4, source_root=self.root)
            candidate = enriched['results'][0]['narrative']
            published = candidate['status'] == 'published' and candidate['publishable'] is True
            note.update(status=candidate['status'], publishable=published,
                        answer=candidate['answer'] if published else None,
                        reason_codes=list(candidate['reason_codes']), next_action=candidate['next_action'],
                        claim_scope=candidate.get('claim_scope'),
                        scope_description=candidate.get('scope_description'))
        except Exception:
            note.update(status='failed', reason_codes=['NOTE_EXECUTION_FAILED'], next_action='inspect_local_diagnostics')
        finally:
            self.remaining -= budget.started
            if budget.started:
                self.last_start = budget.last
            budget.close()
            if original_sdk is not None:
                try:
                    original_sdk.close()
                except Exception:
                    pass  # Cleanup cannot erase a completed scan or trigger regeneration.
        public['requests_started'] = budget.started
        public['session_requests_remaining'] = self.remaining
        return public
