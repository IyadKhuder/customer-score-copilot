"""Fake-clock pacing and request accounting; no network."""
import json
from types import SimpleNamespace as NS
import pytest
from src.request_budget import RequestBudget,RequestBudgetExceeded


class Time:
    def __init__(self):self.now=0.;self.waits=[]
    def clock(self):return self.now
    def sleep(self,n):self.waits.append(n);self.now+=n


def test_spacing_and_budget():
    t=Time();b=RequestBudget(daily_remaining=100,max_requests=3,sleeper=t.sleep,clock=t.clock)
    starts=[]
    for _ in range(3):b.call(lambda:starts.append(t.now))
    assert starts==[0,5,10]
    with pytest.raises(RequestBudgetExceeded):b.call(lambda:pytest.fail('over budget'))
    assert b.started==3


def test_daily_allowance_is_lower_cap():
    b=RequestBudget(daily_remaining=1,max_requests=24)
    b.call(lambda:None)
    with pytest.raises(RequestBudgetExceeded):b.call(lambda:pytest.fail('over daily allowance'))
    assert b.summary()['declared_allowance_remaining']==0


def test_errors_consume_budget_and_log_no_messages(tmp_path):
    b=RequestBudget(daily_remaining=1);p=tmp_path/'requests.jsonl';b.attach_log(p)
    def error():raise ValueError('SECRET')
    with pytest.raises(ValueError):b.call(error)
    b.close();text=p.read_text()
    assert 'SECRET' not in text
    assert [json.loads(x)['event'] for x in text.splitlines()]==['request_start','request_error']
    assert b.started==1


def test_recheck_early_wakeup():
    t=Time()
    def early(n):t.now+=min(n,2)
    b=RequestBudget(daily_remaining=2,sleeper=early,clock=t.clock)
    b.call(lambda:None);b.call(lambda:None)
    assert t.now==5


@pytest.mark.parametrize('kw',[dict(daily_remaining=-1),dict(daily_remaining=501),dict(daily_remaining=True),
 dict(daily_remaining=2,max_requests=0),dict(daily_remaining=2,interval_seconds=4),
 dict(daily_remaining=2,interval_seconds=float('nan'))])
def test_invalid_controls(kw):
    with pytest.raises(ValueError):RequestBudget(**kw)


@pytest.mark.parametrize('kind',['anthropic','openai_compatible'])
def test_sdk_retry_disabled_and_both_endpoints_counted(kind):
    calls=[]
    class SDK:
        max_retries=0
        def with_options(self,**kw):
            assert kw==dict(max_retries=0,timeout=60.0)
            return self
        messages=NS(create=lambda **kw:calls.append(kw))
        chat=NS(completions=NS(create=lambda **kw:calls.append(kw)))
    adapter=NS(client=SDK());b=RequestBudget(daily_remaining=1);b.wrap_adapter(adapter,kind)
    create=adapter.client.messages.create if kind=='anthropic' else adapter.client.chat.completions.create
    create(model='test');assert b.started==1 and calls==[dict(model='test')]


def test_retry_configuration_failure_closed():
    sdk=NS(with_options=lambda **kw:NS(max_retries=2));adapter=NS(client=sdk)
    with pytest.raises(ValueError,match='disable'):RequestBudget(daily_remaining=2).wrap_adapter(adapter,'anthropic')


def test_no_log_overwrite(tmp_path):
    p=tmp_path/'log';p.write_text('old')
    with pytest.raises(FileExistsError):RequestBudget(daily_remaining=2).attach_log(p)
    assert p.read_text()=='old'
