"""Native Slack formatting, literal text safety and transport invariants."""
from copy import deepcopy
from html import unescape
import json
from types import SimpleNamespace

import pytest

from src.slack_presentation import presentation_blocks
from src.slack_listener import SlackReviewListener, format_result, message_parts
from test_slack_listener_83 import Service, Client, event
from test_descriptive_window import service, review


def texts(node):
    if isinstance(node,list):return ''.join(texts(value) for value in node)
    if not isinstance(node,dict) or not node:return ''
    if node.get('type') in ('text','plain_text'):return node['text']
    return texts(node.get('text',{}))+texts(node.get('elements',[]))


def walk(node):
    if isinstance(node,dict):
        yield node
        for value in node.values():yield from walk(value)
    elif isinstance(node,list):
        for value in node:yield from walk(value)


def compact(text):return ''.join(text.split())


def test_account_review_has_heading_emphasis_list_and_monthly_code(service):
    result=review(service,1000025);original=deepcopy(result)
    answer=format_result(result,recovery_question=True)
    blocks=presentation_blocks(answer)
    assert blocks[0]['type']=='header'
    assert blocks[0]['text']['text'].startswith('Account 1000025')
    assert any(n.get('style',{}).get('bold') and n.get('text','').startswith('Yes, partially')
               for n in walk(blocks) if isinstance(n.get('style'),dict))
    assert any(n.get('style',{}).get('italic') and n.get('text','').startswith('Descriptive window:')
               for n in walk(blocks) if isinstance(n.get('style'),dict))
    assert any(n.get('type')=='rich_text_list' and n['style']=='bullet' for n in walk(blocks))
    monthly=next(n for n in walk(blocks) if n.get('type')=='rich_text_preformatted')
    assert texts(monthly).splitlines()==[f'{p[:7]}: {s}' for p,s in zip(result['chart']['periods'],result['chart']['scores'])]
    assert compact(texts(blocks))==compact(answer)
    assert result==original


@pytest.mark.parametrize('text', ['Hi *George* <@U123> <!channel> & `hello`,',
    'Source: <https://evil.invalid|Click>\n```break out```\n@everyone',
    'Review outcome: <!subteam^S123> _injected_ ~format~',
    'Verified note\nUntrusted *text* with <#C123> and ```fences```'])
def test_strings_never_become_mentions_links_or_markdown(text):
    blocks=presentation_blocks(text)
    allowed={'header','plain_text','rich_text','rich_text_section','rich_text_list','rich_text_preformatted','text'}
    assert {n['type'] for n in walk(blocks) if 'type' in n}<=allowed
    assert compact(texts(blocks))==compact(text)
    assert not any('url' in n or 'user_id' in n or 'channel_id' in n for n in walk(blocks))


def test_policy_reference_and_usage_are_formatted_without_changing_text():
    policy='Demo policy reference | v1.0\nSource: POL-001.md | Definition\nBins 4–6 are Standard.'
    assert presentation_blocks(policy)[0]['type']=='header'
    usage='Use: @Score Copilot review account 1000003 as of 1991-12'
    blocks=presentation_blocks(usage)
    assert any(n.get('style',{}).get('code') for n in walk(blocks) if isinstance(n.get('style'),dict))
    assert compact(texts(blocks))==compact(usage)


@pytest.mark.parametrize('text',['',None,'x'*3201,'a\n'*101])
def test_unusual_or_oversized_content_uses_plain_fallback(text):
    assert presentation_blocks(text) is None


def run_listener(command,client=None,service=None):
    client=client or Client();service=service or Service();errors=[]
    listener=SlackReviewListener(service,'C1',sleeper=lambda _:None,charts_enabled=False)
    body=event(text='<@BOT> '+command);body['event'].update(user='U1',thread_ts='parent')
    def run():listener.handle(body,{'bot_user_id':'BOT'},client,SimpleNamespace(error=errors.append))
    return service,client,errors,run


def test_same_thread_plain_fallback_and_exact_once_with_formatted_blocks():
    s,client,errors,run=run_listener('review account 1 as of 2025-02')
    run();run()
    assert s.calls==1 and len(client.sent)==2 and not errors
    for message in client.sent:
        assert message['thread_ts']=='parent' and message['channel']=='C1'
        assert message['mrkdwn'] is False and message['parse']=='none' and message['link_names'] is False
        assert message['blocks']
        assert compact(texts(message['blocks']))==compact(unescape(message['text']))


def test_local_formatting_failure_preserves_text_without_rescanning(monkeypatch):
    import src.slack_listener as module
    def bad(_):raise ValueError('PRIVATE formatting exception')
    monkeypatch.setattr(module,'presentation_blocks',bad)
    s,client,errors,run=run_listener('review account 1 as of 2025-02')
    run();run()
    assert s.calls==1 and len(client.sent)==2 and not errors
    assert all('blocks' not in message for message in client.sent)
    assert 'PRIVATE' not in str(client.sent)


def test_delivery_failure_does_not_resend_or_regenerate():
    s,client,errors,run=run_listener('review account 1 as of 2025-02')
    original=client.chat_postMessage
    def fail(**kw):
        if client.sent:raise RuntimeError('PRIVATE network error')
        original(**kw)
    client.chat_postMessage=fail
    run();run()
    assert s.calls==1 and len(client.sent)==1 and len(errors)==1
    assert 'PRIVATE' not in str(errors)


def test_blocked_note_stays_hidden_in_every_representation():
    result=Service().review('x');result['note'].update(status='blocked',publishable=False,answer='PRIVATE UNVERIFIED')
    assert 'PRIVATE' not in json.dumps(presentation_blocks(format_result(result)))


def test_long_split_preserves_text_and_bounded_valid_blocks():
    original='Verified note\n'+('A verified statement & <literal> *data*.\n'*300)
    parts=message_parts(original)
    restored=[]
    for part in parts:
        blocks=presentation_blocks(unescape(part))
        assert blocks and len(blocks)<=40
        assert compact(texts(blocks))==compact(unescape(part))
        restored.append(unescape(part.split('\n',1)[1]))
    assert ''.join(restored)==original


def test_sdk_posts_native_blocks_and_plain_accessibility_fallback(monkeypatch):
    from slack_sdk import WebClient
    client=WebClient(token='test-token',retry_handlers=[]);calls=[]
    monkeypatch.setattr(client,'api_call',lambda *a,**kw:calls.append((a,kw)) or {'ok':True})
    fallback='Review outcome: follow-up is needed.'
    blocks=presentation_blocks(fallback)
    client.chat_postMessage(channel='C1',thread_ts='parent',text=fallback,blocks=blocks,
                            mrkdwn=False,parse='none',link_names=False,unfurl_links=False,unfurl_media=False)
    payload=calls[0][1]['json']
    assert payload['blocks']==blocks and payload['text']==fallback
    assert payload['mrkdwn'] is False and payload['thread_ts']=='parent'
