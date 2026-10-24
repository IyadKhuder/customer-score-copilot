"""Focused offline coverage for opt-in one-case diagnostic."""
from __future__ import annotations

import pytest
from scripts.diagnose_assisted_case_645 import main, safe_summary


def make_result(reason="A clean passed claim-first audit is required"):
    return {
        'publishable': False,
        'reason_codes': ['COMPOSITION_UNSUPPORTED'],
        'next_action': 'revise_response',
        'tool_rounds': 2,
        'tool_evidence': [{'tool_name':'get_history'}, {'tool_name':'retrieve_policy'},
                          {'tool_name':'evaluate_policy_signals'}],
        'verification': {
            'status':'passed', 'schema':{'status':'passed'},
            'numeric':{'status':'passed'},
            'policy':{'status':'passed', 'checks':[
                {'status':'passed', 'claim':{'field':'warning_signal_status'},
                 'audit':{'checks':[{'field':'warning_signal_status',
                                     'value_matches':True, 'context_matches':True}]}}
            ]},
            'limitations':['untrusted model text must remain hidden'],
            'issues':[], 'rendering':{'status':'incomplete','reason':reason},
            'answer':'DO-NOT-DISPLAY-UNTRUSTED-DRAFT',
        }
    }


def test_prints_safe_status_not_draft():
    text = '\n'.join(safe_summary(make_result()))
    assert 'COMPOSITION_UNSUPPORTED' in text
    assert 'Composer reason: A clean passed claim-first audit is required' in text
    assert 'Limitations present: True' in text
    assert 'Policy check warning_signal_status: passed' in text
    assert 'Value agrees: True' in text
    assert 'DO-NOT-DISPLAY' not in text
    assert 'untrusted model text' not in text


def test_no_audit():
    result = make_result()
    result['verification'] = None
    assert 'No internal verification audit available' in '\n'.join(safe_summary(result))


def test_no_render_reason():
    result = make_result()
    del result['verification']['rendering']['reason']
    assert 'Composer reason:' not in '\n'.join(safe_summary(result))


def test_validation_requires_opt_in():
    with pytest.raises(SystemExit):
        main(['--case','CF-003'])


def test_validation_rejects_non_target_case():
    with pytest.raises(SystemExit):
        main(['--case','CF-001','--confirm-live-api'])


@pytest.mark.parametrize('case_id', ('CF-003','CF-004'))
def test_selects_one_case_and_forwards_assisted_flag(case_id, capsys):
    invoked=[]
    def runner(case, fixture, client, **kwargs):
        invoked.append((case['id'],fixture['scenario_id'], client, kwargs))
        return make_result()
    class Provider:
        pass
    p=Provider()
    assert main(['--case',case_id,'--provider','gemini','--confirm-live-api'],
                client_factory=lambda provider:p,case_runner=runner)==0
    assert len(invoked)==1
    assert invoked[0][0]==case_id
    assert invoked[0][2] is p
    assert invoked[0][3]['policy_tool_suite'] is True
    assert invoked[0][3]['policy_passage_variant']=='clarified_v1'
    output = capsys.readouterr().out
    assert 'Composer reason:' in output
    assert 'DO-NOT-DISPLAY' not in output


def test_execution_error_shows_type_only(capsys):
    def runner(*args, **kwargs):
        raise RuntimeError('SECRET DETAILS NEVER PRINT')
    assert main(['--case','CF-003','--confirm-live-api'],
                client_factory=lambda _:object(),case_runner=runner)==2
    printed=capsys.readouterr().out
    assert 'RuntimeError' in printed
    assert 'SECRET' not in printed
