"""Read-only durable lifecycle summaries and bounded process-local timings.

No case mutation, SDK calls, prompt logging or review package changes.
"""
from collections import Counter, deque
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from math import ceil, isfinite
from statistics import median
from threading import Lock
from time import perf_counter


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def distribution(values, eligible=None):
    values = sorted(v for v in values if isfinite(v) and v >= 0)
    n = len(values)
    return dict(eligible=n if eligible is None else eligible, measured=n,
                missing=0 if eligible is None else eligible - n,
                maximum_ms=max(values) if n else None,
                median_ms=median(values) if n else None,
                p95_ms=values[ceil(.95 * n) - 1] if n else None)


class Timings:
    """Last 10,000 observations, at most 24h; resets on process restart.

    Store only allowlisted labels, timestamps, durations and safe status codes.
    Dropped observations are explicit: this is a retained sample, not full traffic.
    """
    def __init__(self, capacity=10000):
        self.started_at = utcnow()
        self.rows = deque(maxlen=capacity)
        self.lock = Lock()
        self.evicted = 0

    def record(self, kind, label, duration_ms, status):
        if kind not in {'request', 'stage'} or not isfinite(duration_ms) or duration_ms < 0:
            raise ValueError('Invalid measurement')
        if kind == 'stage' and label not in {'submission', 'worker_cycle', 'decision'}:
            raise ValueError('Unknown stage')
        if kind == 'request' and label not in {
                'GET /cases', 'POST /cases', 'GET /cases/{identifier}',
                'GET /cases/{identifier}/evidence', 'POST /cases/{identifier}/decisions'}:
            return
        with self.lock:
            if len(self.rows) == self.rows.maxlen:
                self.evicted += 1
            self.rows.append((utcnow(), kind, label, duration_ms, status))

    @contextmanager
    def stage(self, label):
        started = perf_counter()
        status = 'ok'
        try:
            yield
        except BaseException:
            status = 'error'
            raise
        finally:
            self.record('stage', label, (perf_counter() - started) * 1000, status)

    def summary(self, at):
        start = max(at - timedelta(hours=24), self.started_at)
        with self.lock:
            rows = [r for r in self.rows if start <= r[0] <= at]
            evicted = self.evicted
        groups = []
        for kind, label, status in sorted({(r[1], r[2], r[4]) for r in rows}):
            values = [r[3] for r in rows if (r[1], r[2], r[4]) == (kind, label, status)]
            groups.append(dict(kind=kind, label=label, status=status, **distribution(values)))
        return dict(window_start=start.isoformat(), window_end=at.isoformat(),
                    collection_started_at=self.started_at.isoformat(), capacity=self.rows.maxlen,
                    retained_samples=len(rows), evicted_since_start=evicted, groups=groups,
                    oldest_retained_at=rows[0][0].isoformat() if rows else None,
                    scope='Retained process-local samples; reset on restart. HTTP case routes only. '
                          'Operational-summary, health, static and unknown routes excluded.')


def operational_summary(store, timings, *, at=None):
    at = at or utcnow()
    start = at - timedelta(hours=24)
    # One SQLite read snapshot, all API-owned cases, never queue pagination.
    # Select operational fields only, not evidence, request keys or reviewer data.
    with closing(store.connect()) as db:
        rows = db.execute(
            "SELECT json_extract(c.body,'$.workflow_status'), "
            "json_extract(c.body,'$.created_at'), json_extract(c.body,'$.events') "
            "FROM api_requests r JOIN review_cases c ON r.case_id=c.case_id").fetchall()
    statuses = Counter()
    metrics = {name: [] for name in ('queue_delay', 'processing_elapsed', 'end_to_end',
                                    'review_wait_closed', 'review_wait_open')}
    eligible = Counter()
    reasons = Counter()
    outcomes = Counter()
    submissions = 0
    invalid_events = 0
    import json
    for status, created_raw, events_raw in rows:
        statuses[status if status in {'queued', 'running', 'awaiting_review', 'held',
                                      'failed', 'completed'} else 'OTHER'] += 1
        created = timestamp(created_raw)
        if created is not None and start <= created <= at:
            submissions += 1
        events = json.loads(events_raw)
        running = outcome = review = decision = None
        for event in events:
            when = timestamp(event.get('at'))
            if when is None or when > at:
                invalid_events += 1
                continue
            target = event.get('to_status')
            if target == 'running' and running is None:
                running = when
            if target in {'awaiting_review', 'held', 'failed'} and outcome is None:
                outcome = (when, target, event.get('reason_code'))
            if target == 'awaiting_review' and review is None:
                review = when
            if target == 'completed' and decision is None:
                decision = when

        def add(name, a, b):
            eligible[name] += 1
            if a is not None and b is not None and b >= a:
                metrics[name].append((b - a).total_seconds() * 1000)

        if running is not None and start <= running <= at:
            add('queue_delay', created, running)
        if outcome is not None and start <= outcome[0] <= at:
            outcomes[outcome[1]] += 1
            # Allowlisted operational reasons only; never output arbitrary stored text.
            reason = outcome[2]
            safe = {'VERIFICATION_BLOCKED', 'VERIFICATION_INCOMPLETE',
                    'INTERRUPTED_ANALYSIS_NO_RESULT', 'IMPLEMENTATION_CHANGED',
                    'ANALYSIS_EXECUTION_FAILED', 'INVALID_VERIFICATION_RESULT'}
            if reason:
                reasons[reason if reason in safe else 'OTHER'] += 1
            add('processing_elapsed', running, outcome[0])
            add('end_to_end', created, outcome[0])
        if decision is not None and start <= decision <= at:
            add('review_wait_closed', review, decision)
        if status == 'awaiting_review':
            add('review_wait_open', review, at)
    total = sum(outcomes.values())
    failures = outcomes['failed']
    return dict(window_start=start.isoformat(), window_end=at.isoformat(),
                current_counts=dict(sorted(statuses.items())), cases_total=len(rows),
                submissions_in_window=submissions, outcomes_in_window=dict(outcomes),
                failure_rate=dict(numerator=failures, denominator=total,
                                  value=failures / total if total else None),
                reason_counts=dict(reasons), invalid_event_timestamps=invalid_events,
                lifecycle={k: distribution(v, eligible[k]) for k, v in metrics.items()},
                lifecycle_scope='UTC wall-clock intervals from saved events. Processing elapsed '
                                'may include interruption/downtime; it is not active CPU time. '
                                'Outcome windows use first processing outcome, not analyst completion.',
                timings=timings.summary(at),
                provider=dict(mode='offline', live_notes=False, started_calls=None,
                              token_usage=None, monetary_cost=None,
                              usage_coverage=None,
                              reason='Case-review path has no provider calls or usage/cost collector; '
                                     'missing measurements are unavailable.'),
                active_analysis_time=dict(value=None,
                    reason='No per-analyzer timing hook; worker cycle includes graph/store overhead.'))
