"""Bounded live-note wiring with fake providers; no actual external requests."""
import json
from types import SimpleNamespace

import pytest

from scripts.run_slack_review_83 import parse_options, build_service
from src.slack_review_service import ReviewService
from src.slack_listener import SlackReviewListener
from src.llm_client import OpenAICompatibleClient
from test_batch_notes_713 import report, SDK
from test_slack_listener_83 import Client, event


@pytest.mark.parametrize('argv', [[], ['--confirm-live','--provider','gemini','--daily-remaining','8'],
    ['--confirm-live','--provider','anthropic','--daily-remaining','0']])
def test_valid_options(argv):
    assert parse_options(argv).confirm_live == bool(argv)


@pytest.mark.parametrize('argv', [ ['--provider','gemini'], ['--daily-remaining','8'], ['--confirm-live'],
    ['--confirm-live','--provider','gemini'], ['--confirm-live','--provider','gemini','--daily-remaining','501'],
    ['--max-requests','0'], ['--per-review-max-requests','9'], ['--interval-seconds','nan'],
    ['--interval-seconds','4'], ['--interval-seconds','inf']])
def test_invalid_options(argv):
    with pytest.raises(SystemExit): parse_options(argv)


def test_offline_build_no_provider(tmp_path, monkeypatch):
    import scripts.run_slack_review_83 as startup
    monkeypatch.setattr(startup,'ROOT',tmp_path)
    assert build_service(parse_options([])).factory is None
    assert not (tmp_path/'results').exists()


def test_live_build_lazy_selected_provider(tmp_path, monkeypatch):
    import scripts.run_slack_review_83 as startup
    import src.config as config
    import src.llm_client as llm
    monkeypatch.setattr(startup,'ROOT',tmp_path)
    monkeypatch.setattr(config,'get_provider_config',lambda p: {'adapter':'anthropic','api_key':'PRIVATE'})
    calls=[]
    monkeypatch.setattr(llm,'get_llm_client',lambda p:calls.append(p) or 'adapter')
    service=build_service(parse_options(['--confirm-live','--provider','anthropic','--daily-remaining','8']))
    assert not calls
    assert service.factory()==('adapter','anthropic')
    assert calls==['anthropic'] and service.log_dir.is_dir()


def test_bad_config_sanitized(tmp_path,monkeypatch):
    import scripts.run_slack_review_83 as startup
    import src.config as config
    monkeypatch.setattr(startup,'ROOT',tmp_path)
    def bad(_): raise RuntimeError('PRIVATE API KEY')
    monkeypatch.setattr(config,'get_provider_config',bad)
    with pytest.raises(SystemExit) as caught:
        build_service(parse_options(['--confirm-live','--provider','gemini','--daily-remaining','8']))
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('mode,expected', [('pass','Verified note\n'),('wrong_score','AI-generated note unavailable'),('error','AI-generated note unavailable')])
def test_transport_to_real_verification_with_fake_sdk(tmp_path,mode,expected):
    r=report(tmp_path)
    logs=tmp_path/'logs';logs.mkdir()
    now=[0.0];sdks=[]
    def factory():
        sdk=SDK(r,mode=mode);sdk.close=lambda:None;sdks.append(sdk)
        return OpenAICompatibleClient(sdk,'offline'), 'openai_compatible'
    service=ReviewService(tmp_path/'scores.db',client_factory=factory,daily_remaining=4,
                          session_max_requests=4,log_dir=logs,clock=lambda:now[0],
                          sleeper=lambda t:now.__setitem__(0,now[0]+t))
    client=Client();errors=[]
    listener=SlackReviewListener(service,'C1',sleeper=lambda _:None)
    body=event();logger=SimpleNamespace(error=lambda text:errors.append(text))
    listener.handle(body,{'bot_user_id':'BOT'},client,logger)
    listener.handle(body,{'bot_user_id':'BOT'},client,logger)
    text='\n'.join(m['text'] for m in client.sent)
    assert 'An AI-generated note will be shown only if it passes verification' in text
    assert expected in text and 'The score movements meet a warning rule' in text
    assert 'PRIVATE' not in text and 'Live LLM notes are disabled' not in text
    assert len(sdks)==1 and service.remaining==4-sdks[0].calls
    assert 'LLM requests used:' in text
    log_text=''.join(p.read_text() for p in logs.iterdir())
    assert 'PRIVATE' not in log_text and 'account' not in log_text
    entries=[json.loads(line) for line in log_text.splitlines()]
    assert sum(e['event']=='request_start' for e in entries)==sdks[0].calls
    assert not errors


def test_session_cap_across_messages(tmp_path):
    r=report(tmp_path);calls=[];now=[0.0]
    def factory():
        sdk=SDK(r);sdk.close=lambda:None;calls.append(sdk)
        return OpenAICompatibleClient(sdk,'offline'),'openai_compatible'
    s=ReviewService(tmp_path/'scores.db',client_factory=factory,daily_remaining=8,session_max_requests=4,
                   clock=lambda:now[0],sleeper=lambda t:now.__setitem__(0,now[0]+t))
    for _ in range(2):
        assert s.review('review account 1 as of 2025-02')['note']['publishable']
    assert s.remaining==0
    out=s.review('review account 1 as of 2025-02')
    assert out['status']=='attention_required'
    assert out['note']['reason_codes']==['REQUEST_BUDGET_EXHAUSTED']
    assert len(calls)==2 and sum(c.calls for c in calls)==4


def test_log_failure_preserves_scan_without_requests(tmp_path,monkeypatch):
    report(tmp_path)
    from src.request_budget import RequestBudget
    def bad(*_):raise OSError('PRIVATE')
    monkeypatch.setattr(RequestBudget,'attach_log',bad)
    s=ReviewService(tmp_path/'scores.db',log_dir=tmp_path,client_factory=lambda:pytest.fail('provider created'),daily_remaining=8)
    out=s.review('review account 1 as of 2025-02')
    assert out['status']=='attention_required' and out['note']['status']=='failed'
    assert out['requests_started']==0 and s.remaining==8
