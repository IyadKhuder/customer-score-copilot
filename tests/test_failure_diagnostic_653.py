import json
from pathlib import Path
import pytest
from scripts.diagnose_provider_failures_653 import diagnostic_summary,main
from test_quota_provider_integration_6526 import BudgetSDK
from test_request_budget_6526 import Time
from src.llm_client import AnthropicClient

ROOT=Path(__file__).resolve().parents[1]


def test_safe_fields_no_drafts():
    r=dict(verification=dict(status='passed',answer='PRIVATE',limitations=['PRIVATE'],issues=[],
        numeric=dict(status='passed',checks=[{},{}]),policy=dict(status='passed',checks=[{},{}]),
        rendering=dict(status='incomplete',reason='PRIVATE')),tool_evidence=[])
    out=diagnostic_summary(r,ROOT)
    assert out['limitations_count']==1
    assert out['numeric_checks_count']==2
    assert 'PRIVATE' not in json.dumps(out)


def test_known_composer_reason_retained():
    out=diagnostic_summary(dict(verification=dict(rendering=dict(reason='A clean passed claim-first audit is required'))),ROOT)
    assert out['composer_reason']=='A clean passed claim-first audit is required'


def test_no_audit_does_not_invent_counts():
    out=diagnostic_summary({},ROOT)
    assert out['limitations_count'] is None and out['composer_reason'] is None


def test_consent_before_factory(tmp_path):
    with pytest.raises(SystemExit):main(['--daily-requests-remaining','12','--output-dir',str(tmp_path/'x')],
                                     client_factory=lambda _:pytest.fail('called'))


def test_no_overwrite(tmp_path):
    with pytest.raises(SystemExit):main(['--daily-requests-remaining','12','--output-dir',str(tmp_path),'--confirm-live-api'],
                                     client_factory=lambda _:pytest.fail('called'))


def test_duplicate_cases_rejected(tmp_path):
    with pytest.raises(SystemExit):main(['--cases','CF-002','CF-002','--daily-requests-remaining','12','--output-dir',str(tmp_path/'x'),'--confirm-live-api'])


def test_actual_three_cases_with_fake_sdk(tmp_path,monkeypatch):
    import socket
    from src.experiment_metadata import build_experiment_metadata
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**kw:pytest.fail('NETWORK'))
    plan=build_experiment_metadata(ROOT,provider='anthropic')
    t=Time();client=AnthropicClient(BudgetSDK('anthropic'),plan['provider_configuration']['configured_model'])
    rc=main(['--daily-requests-remaining','12','--output-dir',str(tmp_path/'x'),'--confirm-live-api'],
        client_factory=lambda _:client,sleeper=t.sleep,clock=t.clock)
    summary=json.loads((tmp_path/'x/summary.json').read_text())
    assert rc==0 and len(summary['cases'])==3
    assert summary['request_budget']['requests_started']==6
    assert all(r['diagnostic']['publishable'] for r in summary['cases'])
