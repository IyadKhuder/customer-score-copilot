"""Fake Slack transport plus real offline local service; no live messages."""
from copy import deepcopy
from html import unescape
from types import SimpleNamespace

import pytest

from src.slack_listener import SlackReviewListener, format_result, message_parts, register_listener
from src.slack_review_service import ReviewService
from test_batch_notes_713 import report


class Service:
    calls = 0
    def review(self, command):
        self.calls += 1
        return dict(account_id=1,as_of_period='2025-02-01',market=1,status='attention_required',
                    reason_codes=['WARNING_SIGNAL_TRIGGERED'],next_action='review_warning_signal',
                    coverage_complete=True,missing_baseline_months=[],
                    note=dict(status='not_requested',publishable=False,reason_codes=['LIVE_NOTES_DISABLED']))


class Client:
    def __init__(self): self.sent=[]
    def chat_postMessage(self, **kwargs): self.sent.append(kwargs)


def event(id='E1', text='<@BOT> review account 1 as of 2025-02'):
    return dict(event_id=id,event=dict(type='app_mention',channel='C1',ts='123.1',text=text))


def setup(service=None, **kw):
    service = service or Service()
    client = Client()
    errors=[]
    logger=SimpleNamespace(error=lambda msg:errors.append(msg))
    listener=SlackReviewListener(service,'C1',sleeper=lambda _:None,**kw)
    def run(body): listener.handle(body,{'bot_user_id':'BOT'},client,logger)
    return service,client,listener,run,errors


def test_valid_threaded_and_duplicate():
    service,client,listener,run,_=setup()
    body=event(); body['event']['thread_ts']='parent'
    run(body); run(body)
    assert service.calls==1 and len(client.sent)==2
    assert all(m['thread_ts']=='parent' for m in client.sent)
    assert 'The score movements meet a warning rule' in client.sent[-1]['text']
    assert 'No LLM was called' in client.sent[-1]['text']
    assert all(m['mrkdwn'] is False and m['parse']=='none' and m['link_names'] is False for m in client.sent)


@pytest.mark.parametrize('change', ['other_channel','bot','subtype','other_mention','no_leading_mention','missing_id','no_ts','wrong_type'])
def test_ignored(change):
    service,client,_,run,_=setup(); body=event()
    if change=='other_channel': body['event']['channel']='C2'
    if change=='bot': body['event']['bot_id']='B2'
    if change=='subtype': body['event']['subtype']='bot_message'
    if change=='other_mention': body['event']['text']='<@OTHER> review account 1 as of 2025-02'
    if change=='no_leading_mention': body['event']['text']='hello <@BOT>'
    if change=='missing_id': del body['event_id']
    if change=='no_ts': del body['event']['ts']
    if change=='wrong_type': body['event']['type']='message'
    run(body)
    assert service.calls==0 and not client.sent


@pytest.mark.parametrize('text',['<@BOT>','<@BOT> help','<@BOT> review account 1 as of 2025-99','<@BOT> review account 1 as of 2025-02 <@OTHER>'])
def test_usage_no_service(text):
    service,client,_,run,_=setup(); run(event(text=text))
    assert service.calls==0 and len(client.sent)==1
    assert 'Use:' in client.sent[0]['text']


def test_busy_and_inflight_duplicate():
    service,client,listener,run,_=setup()
    def review(command):
        service.calls+=1
        run(event('E1'))  # Same event while processing: silent.
        run(event('E2'))  # Different event while processing: busy, no queue.
        return dict(status='help')
    service.review=review
    run(event())
    assert service.calls==1
    assert any('A review is running' in m['text'] for m in client.sent)


def test_delivery_failure_never_regenerates():
    service,client,_,run,errors=setup()
    original=client.chat_postMessage
    def fail(**kwargs):
        if client.sent: raise RuntimeError('PRIVATE token')
        original(**kwargs)
    client.chat_postMessage=fail
    run(event()); run(event())
    assert service.calls==1 and len(errors)==1
    assert 'PRIVATE' not in str(errors)


def test_processing_send_failure_does_not_start_review():
    service,client,_,run,_=setup()
    client.chat_postMessage=lambda **_:(_ for _ in ()).throw(RuntimeError('PRIVATE'))
    run(event());run(event())
    assert service.calls==0


def test_service_failure_sanitized_lock_released():
    service,client,listener,run,errors=setup()
    service.review=lambda _:(_ for _ in ()).throw(RuntimeError('PRIVATE'))
    run(event())
    assert 'PRIVATE' not in str(client.sent)+str(errors)
    assert listener.analysis_lock.acquire(False)
    listener.analysis_lock.release()


def test_ttl_capacity_and_active_entry():
    now=[0]
    _,_,listener,_,_=setup(clock=lambda:now[0],ttl=10,capacity=1)
    assert listener._claim('E1')
    now[0]=20
    assert not listener._claim('E2')  # Inflight cannot be evicted or expire.
    listener.events['E1']=30
    assert listener._claim('E2')  # Finished entry can be evicted.
    listener.events['E2']=30
    assert not listener._claim('E2')
    now[0]=31
    assert listener._claim('E2')


def test_long_escaping_preserves_every_character():
    text=('<!channel> <@U123> & https://example.test/ '*400)
    parts=message_parts(text)
    restored=''.join(p.split('\n',1)[1] for p in parts)
    assert unescape(restored)==text
    assert all(len(p)<3100 and '<' not in p and '>' not in p for p in parts)
    _,client,_,run,_=setup()
    run(event())
    assert client.sent[0]['thread_ts']=='123.1'


def test_unpublished_answer_never_displayed():
    result=Service().review('x')
    result['note'].update(status='blocked',answer='PRIVATE UNVERIFIED',publishable=False)
    result['verification']={'answer':'PRIVATE'}
    assert 'PRIVATE' not in format_result(result)
    result['note'].update(status='published',publishable=True,answer='A verified note')
    assert 'A verified note' in format_result(result)


def test_register():
    callbacks={}
    app=SimpleNamespace(event=lambda name:lambda fn:callbacks.setdefault(name,fn))
    _,client,listener,_,_=setup()
    register_listener(app,listener)
    callbacks['app_mention'](event(),{'bot_user_id':'BOT'},client,SimpleNamespace(error=lambda _:None))
    assert len(client.sent)==2


def test_real_service_offline_scan(tmp_path):
    report(tmp_path)
    service=ReviewService(tmp_path/'scores.db')
    _,client,_,run,_=setup(service)
    run(event())
    assert 'The score movements meet a warning rule' in client.sent[-1]['text']
    assert 'No LLM was called' in client.sent[-1]['text']
