"""Real agent/gates/retrieval, fake SDK responses and virtual pacing; no live calls."""
import copy
import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from src import agent
from src.batch_scan import scan_batch, ROOT
from src.batch_notes import add_verified_notes, _tools, _validate_scan
from src.llm_client import OpenAICompatibleClient
from src.request_budget import RequestBudget


def report(tmp_path, scores=None, accounts=(1,)):
    path=tmp_path/'scores.db'
    scores=scores or [600,600,600,600,599,598,597,597]
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE scores(account_id,period,score,bin,market)')
        conn.executemany('INSERT INTO scores VALUES(?,?,?,?,1)',[
            (a,f'{2024+(6+i)//12}-{(6+i)%12+1:02d}-01',s,6)
            for a in accounts for i,s in enumerate(scores)])
    return scan_batch(dict(schema_version=1,account_ids=list(accounts),market=1,as_of_period='2025-02-01'),path)


def budget(limit=8):
    now=[0.0]
    return RequestBudget(daily_remaining=limit,max_requests=max(1,limit),clock=lambda:now[0],
                         sleeper=lambda seconds:now.__setitem__(0,now[0]+seconds))


class SDK:
    """Fake OpenAI SDK exercising the real adapter and per-request budget wrapper."""
    max_retries=99
    def __init__(self, report, *, mode='pass', attack=None):
        self.report=report; self.mode=mode; self.attack=attack; self.calls=0; self.options=[]
        self.chat=NS(completions=NS(create=self.create))
    def with_options(self,**kwargs):
        self.options.append(kwargs); self.max_retries=kwargs['max_retries']; return self
    def create(self,**kwargs):
        self.calls+=1
        if self.mode=='error':
            raise RuntimeError('PRIVATE provider message')
        messages=kwargs['messages']
        # Each chat starts a fresh account-specific conversation.
        user=next(m['content'] for m in messages if isinstance(m,dict) and m['role']=='user')
        account=int(user.split('account ')[1].split(' ')[0])
        r=next(r for r in self.report['results'] if r['account_id']==account)
        if not any(isinstance(m,dict) and m.get('role')=='tool' for m in messages):
            history_args={'account_id':account}
            name='get_history'
            if self.attack=='other_account': history_args['account_id']=999
            if self.attack=='latest': name='get_latest_score'
            calls=[NS(id='h',function=NS(name=name,arguments=json.dumps(history_args)))]
            if self.mode!='no_retrieval':
                calls.append(NS(id='p',function=NS(name='retrieve_policy',arguments=json.dumps({'query':'early warning three consecutive score declines data quality','k':7}))))
            calls.append(NS(id='e',function=NS(name='evaluate_policy_signals',arguments=json.dumps({'account_id':account,'as_of_period':'2025-02-01'}))))
            message=NS(content=None,tool_calls=calls)
        else:
            base=dict(account_id=account,market=1,as_of_period='2025-02-01')
            d=r['policy_decision']; last=r['scan_evidence'][-1]
            payload=dict(answer='PRIVATE UNVERIFIED DRAFT approve credit',limitations=[],
                score_claims=[dict(account_id=account,market=1,period='2025-02-01',metric=k,value=last[k]) for k in ('score','bin')],
                policy_claims=[dict(base,field='warning_signal_status',value=d['warning_signal_status']),
                    dict(base,field='raw_event_count.consecutive_declines',value=sum(e['criterion']=='consecutive_declines' for e in d['events']))])
            if self.mode=='wrong_score': payload['score_claims'][0]['value']=1
            if self.mode=='limitations': payload['limitations']=['Unsupported extra interpretation']
            message=NS(content='not json' if self.mode=='schema' else json.dumps(payload),tool_calls=[])
        return NS(choices=[NS(message=message)])


def run(report, *, mode='pass', attack=None, allowance=8, max_notes=2):
    sdk=SDK(report,mode=mode,attack=attack)
    client=OpenAICompatibleClient(sdk,'offline-test')
    b=budget(allowance)
    out=add_verified_notes(report,client,b,adapter_kind='openai_compatible',max_notes=max_notes)
    assert client.client is sdk
    return out,sdk,b


def test_publish_real_pipeline_and_live_memo_retrieval(tmp_path):
    original=report(tmp_path); frozen=copy.deepcopy(original)
    globals_before=dict(agent.ALL_TOOL_DISPATCH)
    out,sdk,b=run(original)
    n=out['results'][0]['narrative']
    assert n['status']=='published',n
    assert n['publishable'] is True
    assert 'PRIVATE' not in json.dumps(out)
    assert n['policy_passages'] and any(p['source'].startswith('POL-002') for p in n['policy_passages'])
    for passage in n['policy_passages']:
        assert passage['text'] in (ROOT/'policy_memos'/passage['source']).read_text(encoding='utf-8')
    assert b.started==sdk.calls==n['requests_started']==2
    assert sdk.options==[dict(max_retries=0,timeout=60.0)]
    assert original==frozen
    assert agent.ALL_TOOL_DISPATCH==globals_before
    assert out['results'][0]['policy_decision']==original['results'][0]['policy_decision']


@pytest.mark.parametrize('attack',['other_account','latest'])
def test_tool_escape_blocks(tmp_path,attack):
    out,_,_=run(report(tmp_path),attack=attack)
    n=out['results'][0]['narrative']
    assert n['status']=='blocked'
    assert n['reason_codes']==['TOOL_EXECUTION_ERROR']
    assert n['answer'] is None


@pytest.mark.parametrize('mode,code',[('schema','SCHEMA_INVALID'),('wrong_score','NUMERIC_MISMATCH'),('limitations','COMPOSITION_UNSUPPORTED')])
def test_gate_codes_retained(tmp_path,mode,code):
    out,_,_=run(report(tmp_path),mode=mode)
    n=out['results'][0]['narrative']
    assert n['status']=='blocked' and code in n['reason_codes']
    assert n['next_action']!='publish' and n['answer'] is None
    assert 'PRIVATE' not in json.dumps(out)


def test_no_retrieval_cannot_publish(tmp_path):
    out,_,_=run(report(tmp_path),mode='no_retrieval')
    assert out['results'][0]['narrative']['status']=='blocked'


def test_budget_exhausted_mid_case_and_before_next(tmp_path):
    out,sdk,b=run(report(tmp_path,accounts=(1,2)),allowance=1)
    first,second=[r['narrative'] for r in out['results']]
    assert first['status']=='failed' and first['reason_codes']==['REQUEST_BUDGET_EXHAUSTED']
    assert second['status']=='not_requested' and second['reason_codes']==['REQUEST_BUDGET_EXHAUSTED']
    assert sdk.calls==b.started==1


def test_zero_budget_and_note_limit(tmp_path):
    r=report(tmp_path,accounts=(1,2))
    out,sdk,_=run(r,allowance=0)
    assert sdk.calls==0
    assert out['notes']['attempted_count']==0
    out,sdk,_=run(r,max_notes=1)
    assert out['results'][1]['narrative']['reason_codes']==['NOTE_LIMIT_REACHED']
    assert sdk.calls==2


def test_provider_exception_sanitized_and_scan_kept(tmp_path):
    r=report(tmp_path)
    out,sdk,b=run(r,mode='error')
    n=out['results'][0]['narrative']
    assert n['status']=='failed' and n['error_type']=='RuntimeError'
    assert n['answer'] is None and not n['publishable']
    assert 'PRIVATE' not in json.dumps(out)
    assert out['results'][0]['scan_outcome']=='attention_required'
    assert b.started==1


def test_non_attention_skipped(tmp_path):
    out,sdk,b=run(report(tmp_path,scores=[600]*8))
    assert sdk.calls==b.started==0
    assert out['results'][0]['narrative']['status']=='not_requested'


@pytest.mark.parametrize('change',['decision','future','policy'])
def test_inconsistent_scan_refused_before_request(tmp_path,change):
    r=report(tmp_path)
    if change=='decision': r['results'][0]['policy_decision']['warning_signal_status']='not_triggered'
    if change=='future': r['results'][0]['scan_evidence'].append(dict(period='2025-03-01',score=1,bin=1,market=1))
    if change=='policy': r['source']['policy_sha256']['POL-002']='wrong'
    sdk=SDK(r)
    with pytest.raises(ValueError):
        add_verified_notes(r,OpenAICompatibleClient(sdk,'test'),budget(),adapter_kind='openai_compatible')
    assert sdk.calls==0


def test_bound_tools_are_copies_and_refuse_other_context(tmp_path):
    r=report(tmp_path)['results'][0]
    _,dispatch,_=_tools(r['scan_evidence'],1,'2025-02-01',ROOT)
    rows=dispatch['get_history'](account_id=1); rows[-1]['score']=0
    assert dispatch['get_history'](account_id=1)[-1]['score']==597
    with pytest.raises(ValueError): dispatch['get_history'](account_id=True)
    with pytest.raises(ValueError): dispatch['evaluate_policy_signals'](account_id=1,as_of_period='2025-03-01')
    with pytest.raises(ValueError): dispatch['retrieve_policy'](query='warning',k=99)


def test_no_global_mutation_on_provider_error(tmp_path):
    before=dict(agent.ALL_TOOL_DISPATCH)
    run(report(tmp_path),mode='error')
    assert agent.ALL_TOOL_DISPATCH==before


def test_cli_requires_explicit_live_flag(tmp_path):
    out=tmp_path/'out.json'
    command=[sys.executable,'-m','scripts.generate_batch_notes_713','--input','missing.json',
             '--output',str(out),'--provider','gemini','--daily-remaining','8']
    p=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    assert p.returncode==2 and '--confirm-live' in p.stderr
    assert not out.exists()


def test_agent_registry_must_be_complete():
    with pytest.raises(ValueError):
        agent.run_agent('test',None,tool_dispatch={})
    with pytest.raises(ValueError):
        agent.run_agent('test',None,tool_dispatch={},tool_schemas=[{'name':'missing'}])


@pytest.mark.parametrize('scores,scope',[
    ([600]*7+[560],'score_bin_warning_signal_no_declines'),
    ([600]*6+[449,449],'score_bin_warning_signal_quality_hold'),
])
def test_other_claim_scopes(tmp_path,scores,scope):
    out,_,_=run(report(tmp_path,scores=scores))
    n=out['results'][0]['narrative']
    assert n['claim_scope']==scope
    # Existing composers deliberately reject these combinations; do not broaden them.
    assert n['status']=='blocked' and n['reason_codes']==['COMPOSITION_UNSUPPORTED']


def test_exact_date_incompatibility_blocks_without_calls(tmp_path):
    # Scanner permits original non-month-start observations; composers cannot relabel them.
    r=report(tmp_path)
    for row in r['results'][0]['scan_evidence']:
        row['period']=row['period'][:8]+'15'
    out,sdk,_=run(r)
    n=out['results'][0]['narrative']
    assert n['status']=='blocked' and n['reason_codes']==['NOTE_DATE_FORMAT_UNSUPPORTED']
    assert sdk.calls==0


def test_agent_errors_keep_same_reason_codes_as_direct_call(tmp_path):
    r=report(tmp_path)
    schemas,dispatch,_=_tools(r['results'][0]['scan_evidence'],1,'2025-02-01',ROOT)
    sdk=SDK(r,mode='schema')
    direct=agent.run_agent('Produce note for account 1 at February',OpenAICompatibleClient(sdk,'test'),
        verbose=False,publication_mode='claims_first',expected_account_id=1,expected_period='2025-02-01',
        expected_market=1,claim_scope='score_bin_warning_signal_decline_events',tool_schemas=schemas,tool_dispatch=dispatch)
    out,_,_=run(r,mode='schema')
    note=out['results'][0]['narrative']
    assert note['reason_codes']==direct['reason_codes']
    assert note['next_action']==direct['next_action']


def test_sanitized_request_log(tmp_path):
    r=report(tmp_path); sdk=SDK(r,mode='schema'); b=budget()
    log=tmp_path/'requests.jsonl'; b.attach_log(log)
    try:
        add_verified_notes(r,OpenAICompatibleClient(sdk,'test'),b,adapter_kind='openai_compatible')
    finally:
        b.close()
    records=[json.loads(line) for line in log.read_text().splitlines()]
    assert len(records)==4
    assert all(set(record)<= {'event','at_utc','request_number','error_type'} for record in records)
    assert 'PRIVATE' not in log.read_text()


def test_supported_quality_hold_note(tmp_path):
    out,_,_=run(report(tmp_path,scores=[600,600,600,600,449,448,447,447]))
    n=out['results'][0]['narrative']
    assert n['claim_scope']=='score_bin_warning_signal_quality_hold'
    assert n['status']=='published',n
    assert 'pending' in n['answer']


@pytest.mark.parametrize('allowance,expected',[(8,'published'),(1,'failed')])
def test_native_anthropic_budget_and_pipeline(tmp_path,allowance,expected):
    from src.llm_client import AnthropicClient
    class NativeSDK(SDK):
        def __init__(self,r):
            super().__init__(r)
            self.messages=NS(create=self.native_create)
        def native_create(self,**kwargs):
            messages=[dict(m,role='tool') if isinstance(m.get('content'),list) and m['role']=='user' else m
                      for m in kwargs['messages']]
            # The backend transcript generator only needs whether tool results arrived.
            reply=super().create(messages=messages).choices[0].message
            blocks=[NS(type='tool_use',id=t.id,name=t.function.name,input=json.loads(t.function.arguments))
                    for t in reply.tool_calls]
            if reply.content:
                blocks.append(NS(type='text',text=reply.content))
            return NS(content=blocks,stop_reason='end_turn',usage=NS(input_tokens=1,output_tokens=1))
    r=report(tmp_path); sdk=NativeSDK(r); b=budget(allowance)
    out=add_verified_notes(r,AnthropicClient(sdk,'offline-test'),b,adapter_kind='anthropic')
    assert out['results'][0]['narrative']['status']==expected
    assert sdk.calls==b.started==min(2,allowance)
    assert sdk.options==[dict(max_retries=0,timeout=60.0)]


def test_two_notes_do_not_share_account_context(tmp_path):
    out,sdk,b=run(report(tmp_path,accounts=(1,2)))
    assert out['notes']['outcomes']['published']==2
    assert sdk.calls==b.started==4
    assert 'account 1' in out['results'][0]['narrative']['answer']
    assert 'account 2' in out['results'][1]['narrative']['answer']
