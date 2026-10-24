"""Chart isolation, missing-month semantics, real PNG output and fake Slack delivery."""
from copy import deepcopy
from io import BytesIO
import math
import sqlite3
from types import SimpleNamespace

import pytest
from PIL import Image

from src.synthetic_foundations import build_default_anchors
from src.slack_review_service import ReviewService
from src.slack_listener import SlackReviewListener
from src.score_chart import prepare_chart, render_chart, validate_chart
from test_slack_listener_83 import Client, event


@pytest.fixture
def service(tmp_path):
    path=tmp_path/'scores.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE scores(account_id,period,score,bin,market)')
        conn.executemany('INSERT INTO scores VALUES(:account_id,:period,:score,:bin,:market)',
                         [r.as_dict() for r in build_default_anchors()])
    return ReviewService(path)


def chart(service,account=1000008,month='1991-12'):
    return service.review(f'review account {account} as of {month}')['chart']


def test_snapshot_isolation_and_fixed_calendar(service):
    data=chart(service)
    assert data['periods']==[f'1991-{m:02d}-01' for m in range(1,13)]
    assert data['scores'][-6:]==[359,359,359,239,239,239]
    assert data['account_id']==1000008
    assert chart(service,1000001)['scores'][-6:]==[345,348,346,349,347,348]


@pytest.mark.parametrize('account,missing',[(1000009,9),(1000010,11)])
def test_gaps_never_become_zero_or_interpolated(service,account,missing):
    data=chart(service,account)
    assert data['scores'][missing] is None
    assert sum(s is None for s in data['scores'])==1


def test_partial_unknown_unsupported_and_no_history(service):
    assert chart(service,1000011)['scores']==[None]*10+[345,345]
    assert chart(service,1208) is None
    assert chart(service,1000001,'1989-12') is None
    assert chart(service,2000001) is None


def test_future_is_excluded(service):
    data=chart(service,1000008,'1991-09')
    assert data['periods'][-1]=='1991-09-01'
    assert 239 not in data['scores']


def test_quality_flag_does_not_change_decision(service):
    result=service.review('review account 1000007 as of 1991-12')
    assert result['chart']['quality_review'] is True
    assert result['chart']['scores'][-6:]==[200,200,390,200,200,200]
    assert result['next_action']=='data_quality_review'


@pytest.mark.parametrize('field,value',[('scores',[500]*12),('scores',[True]*12),
    ('scores',[None]*12),('scores',[0]*11),('account_id','1'),('quality_review',1)])
def test_malformed_or_offscale_data_rejected(service,field,value):
    data=chart(service);data[field]=value
    with pytest.raises(ValueError):render_chart(data)


def test_wrong_calendar_and_extra_keys_rejected(service):
    data=chart(service);data['periods'][3]=data['periods'][2]
    with pytest.raises(ValueError):validate_chart(data)
    data=chart(service);data['private']='secret'
    with pytest.raises(ValueError):validate_chart(data)


@pytest.mark.parametrize('status',['failed','unsupported'])
def test_bad_snapshot_not_plotted(status):
    assert prepare_chart({'scan_outcome':status}) is None


def test_real_png_and_no_retained_files(service,tmp_path):
    before=set(tmp_path.iterdir());data=chart(service,1000009);original=deepcopy(data)
    png=render_chart(data)
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    with Image.open(BytesIO(png)) as image:
        assert image.size==(1400,700)
        image.verify()
    assert data==original and set(tmp_path.iterdir())==before


def test_rendered_line_has_gap_and_fixed_scale(service,monkeypatch):
    from matplotlib.figure import Figure
    original=Figure.savefig;checked=[]
    def check(fig,*args,**kwargs):
        ax=fig.axes[0]
        assert ax.get_ylim()==(0,449)
        assert math.isnan(ax.lines[0].get_ydata()[9])
        checked.append(True)
        return original(fig,*args,**kwargs)
    monkeypatch.setattr(Figure,'savefig',check)
    render_chart(chart(service,1000009))
    assert checked==[True]


class UploadClient(Client):
    def __init__(self,fail=False):
        super().__init__();self.uploads=[];self.fail=fail
    def files_upload_v2(self,**kwargs):
        self.uploads.append(kwargs)
        if self.fail:raise RuntimeError('PRIVATE upload token')
        return {'ok':True}


def transport(service,client,monkeypatch,*,disabled=False,render_failure=False):
    import src.slack_listener as module
    calls=[];original=service.review
    def once(text):calls.append(text);return original(text)
    monkeypatch.setattr(service,'review',once)
    if render_failure:
        def bad(_):raise RuntimeError('PRIVATE render diagnostic')
        monkeypatch.setattr(module,'render_chart',bad)
    listener=SlackReviewListener(service,'C1',sleeper=lambda _:None,charts_enabled=not disabled)
    body=event(text='<@BOT> review account 1000008 as of 1991-12')
    body['event']['thread_ts']='parent';errors=[]
    listener.handle(body,{'bot_user_id':'BOT'},client,SimpleNamespace(error=errors.append))
    listener.handle(body,{'bot_user_id':'BOT'},client,SimpleNamespace(error=errors.append))
    assert len(calls)==1
    assert 'PRIVATE' not in str(client.sent)+str(errors)
    assert any('severe score drop' in m['text'] for m in client.sent)
    return errors


def test_upload_to_same_thread_once(service,monkeypatch):
    client=UploadClient()
    assert transport(service,client,monkeypatch)==[]
    assert len(client.uploads)==1
    upload=client.uploads[0]
    assert upload['channel']=='C1' and upload['thread_ts']=='parent'
    assert upload['file'].startswith(b'\x89PNG')
    assert upload['filename']=='score_1000008_1991-12.png'
    assert 'Synthetic' in upload['alt_txt']
    assert 'initial_comment' not in upload


@pytest.mark.parametrize('render_failure',[False,True])
def test_failure_keeps_text_and_does_not_retry(service,monkeypatch,render_failure):
    client=UploadClient(fail=True)
    errors=transport(service,client,monkeypatch,render_failure=render_failure)
    assert len(client.uploads)==(0 if render_failure else 1)
    assert len(errors)==1
    assert 'chart could not be attached' in client.sent[-1]['text']


def test_text_only_never_renders_or_uploads(service,monkeypatch):
    client=UploadClient()
    assert transport(service,client,monkeypatch,disabled=True,render_failure=True)==[]
    assert not client.uploads


def test_preparation_failure_keeps_completed_review(service,monkeypatch):
    import src.slack_review_service as module
    def bad(_):raise RuntimeError('PRIVATE')
    monkeypatch.setattr(module,'prepare_chart',bad)
    result=service.review('review account 1000008 as of 1991-12')
    assert result['status']=='attention_required'
    assert result['chart'] is None and result['chart_unavailable']
    assert 'PRIVATE' not in str(result)


def test_launcher_text_only_option():
    from scripts.run_slack_review_83 import parse_options
    assert parse_options([]).no_charts is False
    assert parse_options(['--no-charts']).no_charts is True


def test_pinned_sdk_upload_wrapper_accepts_bytes_and_thread(monkeypatch,service):
    from slack_sdk import WebClient
    from slack_sdk.web.slack_response import SlackResponse
    client=WebClient(token='test-token',retry_handlers=[])
    calls=[]
    def response(data):
        return SlackResponse(client=client,http_verb='POST',api_url='https://example.invalid',
                             req_args={},data=data,headers={},status_code=200)
    def get_url(**kwargs):
        calls.append(('url',kwargs))
        return response({'ok':True,'file_id':'F1','upload_url':'https://example.invalid/upload'})
    def upload(**kwargs):
        calls.append(('bytes',kwargs))
        return SimpleNamespace(status=200)
    def complete(**kwargs):
        calls.append(('complete',kwargs))
        return response({'ok':True,'files':[{'id':'F1'}]})
    monkeypatch.setattr(client,'files_getUploadURLExternal',get_url)
    monkeypatch.setattr(client,'_upload_file',upload)
    monkeypatch.setattr(client,'files_completeUploadExternal',complete)
    png=render_chart(chart(service))
    result=client.files_upload_v2(file=png,filename='scores.png',channel='C1',
                                thread_ts='parent',title='Scores',alt_txt='Synthetic scores')
    assert result['ok']
    assert [name for name,_ in calls]==['url','bytes','complete']
    assert calls[1][1]['data']==png
    assert calls[2][1]['channel_id']=='C1'
    assert calls[2][1]['thread_ts']=='parent'
    assert calls[0][1]['alt_txt']=='Synthetic scores'


def test_unknown_id_and_text_delivery_failure_never_upload(service,monkeypatch):
    client=UploadClient();errors=[]
    listener=SlackReviewListener(service,'C1',sleeper=lambda _:None)
    listener.handle(event(text='<@BOT> review account 1208 as of 1991-12'),
                    {'bot_user_id':'BOT'},client,SimpleNamespace(error=errors.append))
    assert not client.uploads and not errors
    client=UploadClient()
    original=client.chat_postMessage
    def fail_after_ack(**kwargs):
        if client.sent:raise RuntimeError('PRIVATE delivery error')
        return original(**kwargs)
    monkeypatch.setattr(client,'chat_postMessage',fail_after_ack)
    listener=SlackReviewListener(service,'C1',sleeper=lambda _:None)
    listener.handle(event(text='<@BOT> review account 1000008 as of 1991-12'),
                    {'bot_user_id':'BOT'},client,SimpleNamespace(error=errors.append))
    assert not client.uploads
    assert 'PRIVATE' not in str(errors)
