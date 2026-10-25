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
    delivered = '\n'.join(message['text'] for message in client.sent)
    assert 'The score movements meet a warning rule' in delivered
    assert 'No LLM was called' in delivered
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
    delivered = '\n'.join(message['text'] for message in client.sent)
    assert 'The score movements meet a warning rule' in delivered
    assert 'No LLM was called' in delivered


@pytest.mark.parametrize('profile,expected', [
    ({'display_name':'George','real_name':'Other'}, 'Hi George,'),
    ({'display_name':'  ','real_name':'George Smith'}, 'Hi George Smith,'),
    ({'display_name':None,'real_name':'George'}, 'Hi George,'),
    ({}, 'Hi,'),
    ({'display_name':'*George*\n<!channel> <@U123> & `hi`\u202e'},
     'Hi George &lt;!channel&gt; &lt;@U123&gt; &amp; hi,'),
])
def test_personalized_acknowledgement_only(profile,expected):
    service,client,_,run,errors=setup()
    lookups=[];commands=[];original=service.review
    client.users_info=lambda **kw: lookups.append(kw) or {'ok':True,'user':{'profile':profile}}
    service.review=lambda command: commands.append(command) or original(command)
    body=event();body['event'].update(user='U1',thread_ts='parent')
    run(body);run(body)
    assert client.sent[0]['text'].startswith(expected+'\n\nChecking')
    assert not client.sent[1]['text'].startswith('Hi')
    assert lookups==[{'user':'U1'}]
    assert commands==['review account 1 as of 2025-02'] and service.calls==1
    assert all(m['thread_ts']=='parent' for m in client.sent) and not errors


@pytest.mark.parametrize('response', [None, {'ok':False}, {'ok':True,'user':None},
                                     {'ok':True,'user':{'profile':None}}])
def test_name_lookup_failure_preserves_review(response):
    service,client,_,run,errors=setup()
    def lookup(**kw):
        if response is None: raise RuntimeError('PRIVATE profile token')
        return response
    client.users_info=lookup
    body=event();body['event']['user']='U1';run(body)
    assert client.sent[0]['text'].startswith('Hi,\n\n')
    assert service.calls==1 and len(client.sent)==2 and not errors
    assert 'PRIVATE' not in str(client.sent)


def test_name_cache_expiry_capacity_and_failed_lookup_cache():
    now=[0];_,client,listener,_,_=setup(clock=lambda:now[0],name_ttl=10,name_capacity=2)
    calls=[]
    client.users_info=lambda **kw:calls.append(kw['user']) or {'ok':False}
    assert listener._greeting('U1',client)=='Hi,'
    listener._greeting('U1',client)
    listener._greeting('U2',client);listener._greeting('U3',client)
    assert len(listener.names)==2 and 'U1' not in listener.names
    now[0]=11;listener._greeting('U3',client)
    assert calls==['U1','U2','U3','U3'] and len(listener.names)==1
    assert listener._greeting(None,client)=='Hi,'


def test_profile_username_fallback_and_length():
    _,client,listener,_,_=setup()
    client.users_info=lambda **kw:{'ok':True,'user':{'name':'george'}}
    assert listener._greeting('U1',client)=='Hi george,'
    assert len(listener._display_name('x'*200))==80


@pytest.mark.parametrize('kwargs',[{'name_ttl':0},{'name_ttl':float('nan')},
                                   {'name_ttl':float('inf')},{'name_capacity':0}])
def test_invalid_name_cache_bounds(kwargs):
    with pytest.raises(ValueError):setup(**kwargs)


def test_rejected_commands_do_not_lookup_names():
    _,client,listener,run,_=setup()
    client.users_info=lambda **kw:pytest.fail('unexpected lookup')
    body=event();body['event'].update(user='U1',channel='C2');run(body)
    body=event('E2',text='<@BOT> help');body['event']['user']='U1';run(body)
    listener.analysis_lock.acquire()
    try:
        body=event('E3');body['event']['user']='U1';run(body)
    finally:
        listener.analysis_lock.release()
