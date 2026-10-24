"""Prompt clarification never suppresses actual unresolved limitations."""
import json
import pytest
from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import load_fixtures
from src.live_evaluation import run_live_case
from src.policy_assist_suite import SUITE_PROMPT_APPENDICES
from test_assisted_suite_645 import _ToolAwareScriptedProvider
from scripts.run_assisted_benchmark_645 import run_assisted_benchmark
from src.evaluation_metrics import score_report


def test_new_prompt_contract_keeps_gaps_and_no_fixture_answers():
    for scope in ('score_bin_warning_signal_no_declines','grace_decline_events'):
        text=SUITE_PROMPT_APPENDICES[scope]
        assert 'STRUCTURED LIMITATIONS CONTRACT' in text
        assert 'retain a specific limitation and omit unsupported claims' in text
        for value in ('CF-002','CF-005','CF-006','81000001','83000005'):
            assert value not in text


@pytest.mark.parametrize('index',[1,4,5])
def test_real_unresolved_limitation_still_blocks(index):
    class MissingEvidence(_ToolAwareScriptedProvider):
        def continue_with_tool_results(self,**kw):
            response=super().continue_with_tool_results(**kw)
            data=json.loads(response.text)
            data['limitations']=['Required evidence remains unresolved.']
            response.text=json.dumps(data)
            return response
    b=load_benchmark();f=load_fixtures(benchmark=b)
    c=b['cases'][index]
    fixture=next(x for x in f['fixtures'] if x['scenario_id']==c['scenario_id'])
    out=run_live_case(c,fixture,MissingEvidence(),policy_passage_variant='clarified_v1',policy_tool_suite=True)
    assert out['publishable'] is False
    assert out['reason_codes']==['COMPOSITION_UNSUPPORTED']
    assert out['verification']['limitations']==['Required evidence remains unresolved.']


def test_real_six_case_contract_unchanged():
    run=run_assisted_benchmark(_ToolAwareScriptedProvider(),provider_id='scripted.prompt_refinement')
    scores=score_report(run)
    assert scores['metrics']['cases_passed_all_checks']==6
    assert run['cases'][3]['observed']['reason_codes']==['POLICY_EVIDENCE_INSUFFICIENT']
    assert run['cases'][3]['observed']['next_action']=='gather_evidence'
