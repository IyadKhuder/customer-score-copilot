"""Full helper/adapter/gate path with request budget and fake SDKs."""
import json
import socket
from pathlib import Path
import pytest
from test_provider_experiment_6525 import SDK,ROOT
from src.llm_client import AnthropicClient,OpenAICompatibleClient
from src.experiment_metadata import build_experiment_metadata
from src.provider_experiment import run_provider_experiment
from src.request_budget import RequestBudget
from test_request_budget_6526 import Time


class BudgetSDK(SDK):
    max_retries=0
    def with_options(self,**kw):
        assert kw==dict(max_retries=0,timeout=60.0)
        return self


@pytest.mark.parametrize('provider',['anthropic','gemini'])
def test_full_six_case_budgeted_execution(tmp_path,monkeypatch,provider):
    def forbidden(*a,**kw):raise AssertionError('network/database')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr('src.score_tools._connect',forbidden)
    t=Time();budget=RequestBudget(daily_remaining=24,max_requests=24,sleeper=t.sleep,clock=t.clock)
    plan=build_experiment_metadata(ROOT,provider=provider,between_case_seconds=0,retry_delay_seconds=0)
    sdk=BudgetSDK(provider)
    adapter=(AnthropicClient if provider=='anthropic' else OpenAICompatibleClient)(sdk,plan['provider_configuration']['configured_model'])
    report,scores,meta=run_provider_experiment(plan,ROOT,tmp_path/'bundle',confirm_live_api=True,
            client_factory=lambda _:adapter,sleeper=t.sleep,clock=t.clock,request_budget=budget)
    assert scores['metrics']['cases_passed_all_checks']==6
    assert budget.started==12 and t.now==55
    assert meta['execution_record']['request_budget']['requests_started']==12
    assert len((tmp_path/'bundle/requests.jsonl').read_text().splitlines())==24
    assert 'UNTRUSTED DRAFT' not in json.dumps(meta)


def test_exhaustion_stops_requests_but_saves_report(tmp_path,monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**kw:pytest.fail('NETWORK'))
    t=Time();b=RequestBudget(daily_remaining=1,max_requests=24,sleeper=t.sleep,clock=t.clock)
    plan=build_experiment_metadata(ROOT,provider='anthropic',between_case_seconds=0,retry_delay_seconds=0)
    sdk=BudgetSDK('anthropic');adapter=AnthropicClient(sdk,plan['provider_configuration']['configured_model'])
    report,scores,meta=run_provider_experiment(plan,ROOT,tmp_path/'bundle',confirm_live_api=True,
          client_factory=lambda _:adapter,sleeper=t.sleep,clock=t.clock,request_budget=b)
    assert b.started==1
    assert all(r['error_type']=='RequestBudgetExceeded' for r in report['cases'])
    assert scores['metrics']['cases_passed_all_checks']==0
    assert (tmp_path/'bundle/metadata.json').exists()
