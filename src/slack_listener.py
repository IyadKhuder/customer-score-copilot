"""Socket Mode listener logic, independently testable without Slack or network."""
from collections import OrderedDict
from html import escape
from threading import Lock
from time import monotonic, sleep
import re

from src.slack_review_service import parse_request, USAGE


OUTCOMES = {
    'attention_required': 'Attention required: a supported review indication or data-quality flag was found.',
    'no_signal_in_scope': 'No signal within this scan\'s scope. This is not comprehensive policy clearance.',
    'insufficient_data': 'Insufficient evidence for this scoped assessment.',
    'unsupported': 'Unsupported account history: this demo supports market 1 only.',
    'failed': 'The review could not be completed.',
}
REASONS = {
    'WARNING_SIGNAL_TRIGGERED': 'Warning signal triggered',
    'DATA_QUALITY_REVIEW_REQUIRED': 'Data-quality review required',
    'COMMITTEE_REVIEW_INDICATED': 'Committee review indicated',
    'ANALYST_REFERRAL_INDICATED': 'Analyst referral indicated',
    'SIGNAL_PENDING_DATA_QUALITY': 'Signal pending data-quality review',
}


def format_result(result):
    """Reviewed templates over the service's public projection only."""
    status = result['status']
    if status in ('invalid_request', 'help'):
        return USAGE
    if status == 'busy':
        return 'A review is running. Try again after it finishes.'
    lines = [f"Account {result['account_id']} | as of {result['as_of_period'][:7]} | market 1",
             OUTCOMES.get(status, 'The review could not be completed.')]
    for code in result.get('reason_codes', []):
        lines.append(f"Reason: {REASONS.get(code, code)} ({code})" if code in REASONS else f'Reason: {code}')
    coverage = result.get('coverage_complete')
    if coverage is not None:
        lines.append('Required scan baseline: ' + ('complete' if coverage else 'incomplete'))
    if result.get('missing_baseline_months'):
        lines.append('Missing baseline months: ' + ', '.join(result['missing_baseline_months']))
    lines.append('Next step: ' + result.get('next_action', 'manual_review').replace('_', ' '))
    note = result.get('note', {})
    if note.get('status') == 'published' and note.get('publishable') is True and note.get('answer'):
        lines.extend(['', 'Verified note', note.get('scope_description') or 'Only the stated verified claim scope is covered.', note['answer']])
    elif 'LIVE_NOTES_DISABLED' in note.get('reason_codes', []):
        lines.extend(['', 'Verified note not requested: live LLM notes are disabled. No LLM was called.'])
    elif status == 'attention_required':
        lines.extend(['', 'Verified note unavailable.'])
        lines.extend('Note reason: ' + code for code in note.get('reason_codes', []))
    if 'requests_started' in result:
        lines.append(f"Provider requests this review: {result['requests_started']}; session allowance remaining: {result['session_requests_remaining']}")
    lines.extend(['', 'Scope: single-account market-1 scan; no causal explanation or credit action is authorized.'])
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
            send('Reviewing the account snapshot. A bounded verified note may be attempted.'
                 if getattr(self.service, 'factory', None) is not None
                 else 'Reviewing the account snapshot. Live LLM notes are disabled.')
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
