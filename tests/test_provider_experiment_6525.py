"""Real six-case assisted agent/gate through actual adapters and fake SDKs.

The scripted model is the existing 6.4.5 test helper, not a real provider.
No policy evaluator, live-case path, agent, composer or gate is mocked.
"""
import copy
import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from src.experiment_metadata import build_experiment_metadata
from src.llm_client import AnthropicClient, OpenAICompatibleClient
from src.provider_experiment import run_provider_experiment
from scripts.run_provider_experiment_6525 import main
from test_assisted_suite_645 import _ToolAwareScriptedProvider

ROOT=Path(__file__).resolve().parents[1]


class SDK:
    def __init__(self,kind):
        self.kind=kind;self.core=_ToolAwareScriptedProvider();self.last=None;self.requests=[]
        self.chat=NS(completions=NS(create=self.create));self.messages=NS(create=self.create)
    def create(self,**kw):
        self.requests.append(copy.deepcopy(kw))
        if self.kind=='gemini':
            tools=[dict(name=t['function']['name'],description=t['function']['description'],
                        input_schema=t['function']['parameters']) for t in kw['tools']]
            messages=kw['messages']
            fresh=not any(not isinstance(m,dict) for m in messages)
        else:
            tools=kw['tools'];messages=[dict(role='system',content=kw['system'])]+kw['messages']
            fresh=not any(m['role']=='assistant' for m in kw['messages'])
        assert {t['name'] for t in tools}=={'get_history','retrieve_policy','evaluate_policy_signals'}
        schema=next(t for t in tools if t['name']=='evaluate_policy_signals')
        assert schema['input_schema']['required']==['account_id','as_of_period']
        if fresh:
            self.last=self.core.chat(messages=messages,tools=tools)
        else:
            if self.kind=='gemini':
                payloads=[json.loads(m['content']) for m in messages if isinstance(m,dict) and m.get('role')=='tool'][-3:]
            else:
                payloads=[json.loads(b['content']) for b in kw['messages'][-1]['content']]
            self.last=self.core.continue_with_tool_results(assistant_response=self.last,tool_results=payloads,tools=tools)
        r=self.last
        if self.kind=='gemini':
            message=NS(content=r.text,tool_calls=[NS(id=t.id,function=NS(name=t.name,arguments=json.dumps(t.arguments))) for t in r.tool_calls or []] or None)
            return NS(choices=[NS(message=message,finish_reason='tool_calls' if r.tool_calls else 'stop')])
        content=([NS(type='text',text=r.text)] if r.text else [])+[
            NS(type='tool_use',id=t.id,name=t.name,input=t.arguments) for t in r.tool_calls or []]
        return NS(content=content,stop_reason='tool_use' if r.tool_calls else 'end_turn',usage=NS(input_tokens=1,output_tokens=1))


@pytest.fixture(autouse=True)
def forbid_network_and_sql(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Network or database accessed')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr('src.score_tools._connect',forbidden)


def execute(tmp_path,provider='anthropic',plan=None):
    plan=plan or build_experiment_metadata(ROOT,provider=provider,between_case_seconds=0,retry_delay_seconds=0)
    sdk=SDK(provider)
    adapter=(AnthropicClient if provider=='anthropic' else OpenAICompatibleClient)(sdk,plan['provider_configuration']['configured_model'])
    bundle=tmp_path/'bundle'
    result=run_provider_experiment(plan,ROOT,bundle,confirm_live_api=True,client_factory=lambda _:adapter,
                                   sleeper=lambda _:None,clock=lambda:0.0)
    return (*result,sdk,bundle)


@pytest.mark.parametrize('provider',['gemini','anthropic'])
def test_real_agent_adapter_helper_verifier_six_cases(tmp_path,provider):
    report,scores,meta,sdk,bundle=execute(tmp_path,provider)
    assert scores['metrics']['cases_passed_all_checks']==6
    assert [r['observed']['publishable'] for r in report['cases']]==[True,True,True,False,True,True]
    neg=report['cases'][3]['observed']
    assert neg['reason_codes']==['POLICY_EVIDENCE_INSUFFICIENT']
    assert neg['next_action']=='gather_evidence'
    assert all('evaluate_policy_signals' in r['observed']['tool_names'] for r in report['cases'])
    assert len(sdk.requests)==12
    assert meta['record_kind']=='injected_adapter_execution'
    assert not meta['live_provider_execution_verified']
    assert 'UNTRUSTED DRAFT' not in json.dumps(report)


def test_schema_matches_between_adapter_transports(tmp_path):
    a=tmp_path/'a';a.mkdir();b=tmp_path/'b';b.mkdir()
    *_,ga,_=execute(a,'gemini');*_,an,_=execute(b,'anthropic')
    g=[dict(name=t['function']['name'],description=t['function']['description'],input_schema=t['function']['parameters']) for t in ga.requests[0]['tools']]
    assert g==an.requests[0]['tools']


def test_bound_saved_byte_hashes(tmp_path):
    report,scores,m,_,p=execute(tmp_path)
    for f,k in [('observations.json','report_sha256'),('scores.json','scores_sha256')]:
        assert hashlib.sha256((p/f).read_bytes()).hexdigest()==m['execution_record'][k]
    assert json.loads((p/'metadata.json').read_text())==m


def test_no_authorization_no_factory(tmp_path):
    with pytest.raises(ValueError,match='confirm_live_api'):
        run_provider_experiment({},ROOT,tmp_path/'x',client_factory=lambda _:pytest.fail('factory called'))
    assert not (tmp_path/'x').exists()


def test_stale_plan_no_factory(tmp_path):
    plan=build_experiment_metadata(ROOT,provider='anthropic')
    plan['source_snapshot_sha256']='0'*64
    with pytest.raises(ValueError,match='stale'):
        run_provider_experiment(plan,ROOT,tmp_path/'x',confirm_live_api=True,client_factory=lambda _:pytest.fail('factory called'))
    assert not (tmp_path/'x').exists()


def test_existing_bundle_no_factory(tmp_path):
    p=tmp_path/'bundle';p.mkdir();(p/'old.txt').write_text('preserve')
    plan=build_experiment_metadata(ROOT,provider='anthropic')
    with pytest.raises(FileExistsError):
        run_provider_experiment(plan,ROOT,p,confirm_live_api=True,client_factory=lambda _:pytest.fail('factory called'))
    assert (p/'old.txt').read_text()=='preserve'


def test_wrong_constructed_model_rejected(tmp_path):
    plan=build_experiment_metadata(ROOT,provider='anthropic')
    with pytest.raises(ValueError,match='class/model'):
        run_provider_experiment(plan,ROOT,tmp_path/'x',confirm_live_api=True,
            client_factory=lambda _:AnthropicClient(SDK('anthropic'),'wrong'))
    assert not (tmp_path/'x/metadata.json').exists()


def test_cli_requires_confirmation_before_factory(tmp_path):
    with pytest.raises(SystemExit):
        main(['--plan',str(tmp_path/'none.json'),'--output-dir',str(tmp_path/'x')],
             client_factory=lambda _:pytest.fail('factory called'))


def test_cli_refuses_existing_destination(tmp_path):
    with pytest.raises(SystemExit):
        main(['--plan',str(tmp_path/'none.json'),'--output-dir',str(tmp_path),'--confirm-live-api'],
             client_factory=lambda _:pytest.fail('factory called'))


def test_current_plan_input_not_mutated(tmp_path):
    plan=build_experiment_metadata(ROOT,provider='anthropic',between_case_seconds=0,retry_delay_seconds=0)
    old=copy.deepcopy(plan);execute(tmp_path,plan=plan);assert plan==old


def test_wrong_root_rejected(tmp_path):
    with pytest.raises(ValueError,match='source tree'):
        run_provider_experiment({},tmp_path,tmp_path/'x',confirm_live_api=True)
