"""Serial per-request pacing and a conservative, caller-owned quota allowance."""
import json
import math
from datetime import datetime, timezone
from time import monotonic, sleep
from types import SimpleNamespace


class RequestBudgetExceeded(RuntimeError):
    pass


class RequestBudget:
    def __init__(self, *, daily_remaining, max_requests=24, interval_seconds=5.0,
                 sleeper=sleep, clock=monotonic):
        if type(daily_remaining) is not int or not 0 <= daily_remaining <= 500:
            raise ValueError('daily_remaining must be an integer in 0..500')
        if type(max_requests) is not int or not 1 <= max_requests <= 500:
            raise ValueError('max_requests must be an integer in 1..500')
        if (type(interval_seconds) not in (int,float) or not math.isfinite(interval_seconds)
                or interval_seconds < 5):
            raise ValueError('Request interval must be finite and at least 5 seconds')
        if not callable(sleeper) or not callable(clock):
            raise TypeError('Clock and sleeper must be callable')
        self.limit=min(daily_remaining,max_requests)
        self.daily_remaining=daily_remaining
        self.max_requests=max_requests
        self.interval=float(interval_seconds)
        self.sleep,self.clock=sleeper,clock
        self.started=0;self.last=None;self.errors={};self._stream=None

    def attach_log(self, path):
        if self._stream is not None or self.started:
            raise ValueError('Budget must be fresh and unattached')
        self._stream=path.open('x',encoding='utf-8')

    def close(self):
        if self._stream is not None:self._stream.close()

    def _log(self,event,**fields):
        if self._stream is not None:
            self._stream.write(json.dumps(dict(event=event,at_utc=datetime.now(timezone.utc).isoformat(),**fields))+'\n')
            self._stream.flush()

    def call(self, create, **kwargs):
        if self.started >= self.limit:
            raise RequestBudgetExceeded('Request allowance exhausted')
        if self.last is not None:
            # Recheck after sleep so early wakeups cannot violate spacing.
            while True:
                remaining=self.interval-(self.clock()-self.last)
                if remaining<=0:break
                self.sleep(remaining)
        self.started+=1
        self.last=self.clock()
        self._log('request_start',request_number=self.started)
        try:
            result=create(**kwargs)
        except Exception as exc:
            name=type(exc).__name__
            self.errors[name]=self.errors.get(name,0)+1
            self._log('request_error',request_number=self.started,error_type=name)
            raise
        self._log('request_returned',request_number=self.started)
        return result

    def wrap_adapter(self,adapter,kind):
        # SDK retries otherwise occur below this application-level counter.
        configured=adapter.client.with_options(max_retries=0,timeout=60.0)
        if configured.max_retries != 0:
            raise ValueError('Could not disable SDK retries')
        if kind=='anthropic':
            create=configured.messages.create
            adapter.client=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw:self.call(create,**kw)))
        elif kind=='openai_compatible':
            create=configured.chat.completions.create
            adapter.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:self.call(create,**kw))))
        else:raise ValueError('Unsupported adapter')
        return configured

    def summary(self):
        return dict(max_requests=self.max_requests,daily_remaining_declared=self.daily_remaining,
                    effective_request_limit=self.limit,requests_started=self.started,
                    declared_allowance_remaining=self.daily_remaining-self.started,
                    minimum_request_start_interval_seconds=self.interval,
                    sdk_max_retries=0,sdk_timeout_seconds=60.0,error_type_counts=dict(self.errors),
                    scope='This serial process only. Declared daily allowance is not verified account usage; no automatic daily reset.')
