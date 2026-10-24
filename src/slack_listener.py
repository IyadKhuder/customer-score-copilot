"""Socket Mode listener logic, independently testable without Slack or network."""
from collections import OrderedDict
from html import escape
from threading import Lock
from time import monotonic, sleep
import re

from src.slack_review_service import parse_request, USAGE


OUTCOMES = {
    'attention_required': 'Review outcome: follow-up is needed.',
    'no_signal_in_scope': 'Review outcome: no warning or escalation was found by this scan. This is not an all-clear assessment.',
    'insufficient_data': 'Review outcome: there is not enough information to complete this review.',
    'unsupported': 'This account is outside this demo’s supported market (market 1).',
    'failed': 'The review could not be completed. Check the local data and setup.',
}
REASONS = {
    'NO_HISTORY_AS_OF': 'This account exists, but no observations are available on or before the requested month.',
    'MISSING_AS_OF_OBSERVATION': 'This account exists, but its observation for the requested month is missing.',
    'WARNING_SIGNAL_TRIGGERED': 'The score movements meet a warning rule and need review.',
    'DATA_QUALITY_REVIEW_REQUIRED': 'Check the recorded data before interpreting the score movements.',
    'COMMITTEE_REVIEW_INDICATED': 'The escalation rules call for committee review.',
    'ANALYST_REFERRAL_INDICATED': 'The escalation rules also call for an analyst review.',
    'SIGNAL_PENDING_DATA_QUALITY': 'Some potential warning signals are on hold until the data is checked.',
    'INCOMPLETE_BASELINE': 'Some months needed for the full review are missing.',
}
ACTIONS = {
    'none': 'No follow-up is indicated by this scan.',
    'review_warning_signal': 'Ask an analyst to review the detected warning.',
    'committee_review': 'Refer the account for committee review.',
    'analyst_review': 'Refer the account to an analyst.',
    'data_quality_review': 'Verify the suspect observations, correct them if necessary, and repeat the review.',
    'gather_evidence': 'Check or supply the missing monthly observations, then repeat the review.',
    'use_supported_market': 'Use an account from market 1.',
    'repair_history': 'Check and repair the account history before trying again.',
    'inspect_error': 'Inspect the local setup and diagnostics.',
    'inspect_local_sources': 'Check the local database and policy files.',
}


def format_result(result):
    """Plain-language templates; private evidence and unpublished drafts stay hidden."""
    status = result['status']
    if status in ('invalid_request', 'help'):
        return USAGE
    if status == 'busy':
        return 'A review is running. Try again after it finishes.'
    if 'ACCOUNT_NOT_FOUND' in result.get('reason_codes', []):
        return (f"Account ID {result['account_id']} was not found in the current database. "
                "Please verify the account ID and try again.")
    lines = [f"Account {result['account_id']} | review month: {result['as_of_period'][:7]} | market 1"]
    lines.extend(result.get('score_summary', []))
    lines.extend(['', OUTCOMES.get(status, 'The review could not be completed.')])
    for code in result.get('reason_codes', []):
        if code in REASONS:
            lines.append(REASONS[code])
    coverage = result.get('coverage_complete')
    if coverage is not None:
        lines.append('Data available for the required review period: ' + ('complete.' if coverage else 'incomplete.'))
    if result.get('missing_baseline_months'):
        lines.append('Missing months: ' + ', '.join(p[:7] for p in result['missing_baseline_months']))
    lines.append('Next step: ' + ACTIONS.get(result.get('next_action'), 'Ask an analyst to inspect the local review result.'))
    note = result.get('note', {})
    if note.get('status') == 'published' and note.get('publishable') is True and note.get('answer'):
        lines.extend(['', 'Verified note', note.get('scope_description') or 'Only the stated verified claim scope is covered.', note['answer']])
    elif 'LIVE_NOTES_DISABLED' in note.get('reason_codes', []):
        lines.extend(['', 'AI-generated note: disabled. No LLM was called.'])
    elif status == 'attention_required':
        reasons = note.get('reason_codes', [])
        if 'REQUEST_BUDGET_EXHAUSTED' in reasons:
            message = 'the configured request allowance has been used up.'
        elif note.get('status') == 'blocked':
            message = 'the draft did not pass all publication checks.'
        elif note.get('status') == 'failed':
            message = 'the note could not be generated or verified.'
        else:
            message = 'no verified note is available for this review.'
        lines.extend(['', 'AI-generated note unavailable: ' + message])
    if 'requests_started' in result:
        lines.append(f"LLM requests used: {result['requests_started']}; session allowance remaining: {result['session_requests_remaining']}")
    lines.extend(['', 'This describes recorded score movements and selected review rules for market 1. '
                  'It does not explain their cause, establish recovery eligibility, or authorize a credit decision.'])
    return '\n'.join(lines)


def message_parts(text, limit=3000):
    """Escape Slack control characters and split without breaking entities."""
    parts, current, size = [], [], 0
    for char in text:
        encoded = escape(char, quote=False)
        if size + len(encoded) > limit:
            parts.append(''.join(current))
            current, size = [], 0
        current.append(encoded)
        size += len(encoded)
    if current:
        parts.append(''.join(current))
    if len(parts) > 1:
        parts = [f'Part {i}/{len(parts)}\n{part}' for i, part in enumerate(parts, 1)]
    return parts


class SlackReviewListener:
    """Bounded event cache; one analysis, no queued analyses or regeneration.

    Cache survives only within this process. Finished entries expire after 1 hour,
    and oldest finished entries may be evicted when the 1000-event cap is reached.
    In-flight entries are never evicted. Expired/evicted events may execute again.
    """
    def __init__(self, service, allowed_channel, *, clock=monotonic, sleeper=sleep,
                 ttl=3600, capacity=1000):
        if not allowed_channel or type(capacity) is not int or capacity < 1 or ttl <= 0:
            raise ValueError('Require channel and positive cache bounds')
        self.service, self.channel = service, allowed_channel
        self.clock, self.sleep, self.ttl, self.capacity = clock, sleeper, ttl, capacity
        self.events = OrderedDict()
        self.cache_lock, self.analysis_lock = Lock(), Lock()

    def _claim(self, event_id):
        with self.cache_lock:
            now = self.clock()
            for key, expiry in list(self.events.items()):
                if expiry <= now:
                    del self.events[key]
            if event_id in self.events:
                return False
            if len(self.events) >= self.capacity:
                done = next((k for k, expiry in self.events.items() if expiry != float('inf')), None)
                if done is None:
                    return False
                del self.events[done]
            self.events[event_id] = float('inf')
            return True

    def handle(self, body, context, client, logger):
        event = body.get('event', {})
        if (event.get('type') != 'app_mention' or event.get('channel') != self.channel
                or event.get('bot_id') or event.get('subtype') or not event.get('ts')):
            return
        bot = context.get('bot_user_id')
        text = event.get('text')
        if not bot or not isinstance(text, str):
            return
        match = re.match(r'^\s*<@' + re.escape(bot) + r'>\s*', text)
        if not match or not isinstance(body.get('event_id'), str) or not body['event_id']:
            return
        if not self._claim(body['event_id']):
            return
        thread = event.get('thread_ts') or event['ts']
        acquired = False
        def send(text):
            for i, part in enumerate(message_parts(text)):
                if i:
                    self.sleep(1.1)
                client.chat_postMessage(channel=self.channel, thread_ts=thread, text=part,
                                        mrkdwn=False, parse='none', link_names=False,
                                        unfurl_links=False, unfurl_media=False)
        try:
            command = text[match.end():]
            try:
                request = parse_request(command)
            except ValueError:
                send(USAGE)
                return
            if request is None:
                send(USAGE)
                return
            acquired = self.analysis_lock.acquire(blocking=False)
            if not acquired:
                send('A review is running. Try again after it finishes.')
                return
            send('Checking the account history. An AI-generated note will be shown only if it passes verification.'
                 if getattr(self.service, 'factory', None) is not None
                 else 'Checking the account history. No LLM will be called.')
            try:
                result = self.service.review(command)
                answer = format_result(result)
            except Exception:
                logger.error('Local review failed; private diagnostics were not sent to Slack.')
                answer = 'The local review could not be completed. Check the local setup; no automatic retry was made.'
            self.sleep(1.1)
            send(answer)
        except Exception:
            # A delivery failure never reruns the service or includes exception text.
            logger.error('Slack reply delivery failed. The review will not be regenerated automatically.')
        finally:
            if acquired:
                self.analysis_lock.release()
            with self.cache_lock:
                self.events[body['event_id']] = self.clock() + self.ttl


def register_listener(app, listener):
    @app.event('app_mention')
    def on_mention(body, context, client, logger):
        listener.handle(body, context, client, logger)
