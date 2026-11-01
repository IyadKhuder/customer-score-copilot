"""Decision identity, package binding, atomicity and durable recovery checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import subprocess
import sys
from threading import Barrier

import pytest

from src.review_graph import (config, create_case, inspect_case, open_graph,
                              resume_case, submit_decision)
from src.review_workflow import CaseStore, TransitionError, resume


def payload_for(case, decision='approved', **changes):
    payload = dict(case_id=case['case_id'], expected_revision=case['revision'],
                   package_hash=case['package_hash'], decision=decision,
                   reviewer='demo-analyst', reason='Reviewed the saved synthetic package.')
    payload.update(changes)
    return payload


def ready(store, graph):
    cid = create_case(store, graph)['case']['case_id']
    return resume_case(store, graph, cid)['case']


@pytest.mark.parametrize('choice', ['approved', 'rejected'])
def test_decision_survives_process_restart(tmp_path, choice):
    path = tmp_path / 'review.sqlite'
    def cli(*args, expected=0):
        result = subprocess.run([sys.executable, '-m', 'src.review_graph', '--store', str(path),
                                 *args], text=True, capture_output=True)
        assert result.returncode == expected, result.stderr
        return json.loads(result.stdout) if result.returncode == 0 else result.stderr
    cid = cli('create')['case']['case_id']
    before = cli('resume', cid)
    case = before['case']
    args = ('decide', cid, '--decision', choice, '--reviewer', 'demo-analyst',
            '--reason', 'Checked exact synthetic review package.',
            '--expected-revision', str(case['revision']), '--package-hash', case['package_hash'])
    result = cli(*args)
    saved = result['case']
    assert saved['workflow_status'] == 'completed'
    assert saved['analyst_decision'] == choice
    assert saved['verification_status'] == 'passed'
    assert saved['revision'] == 3
    assert saved['decision']['reviewed_revision'] == 2
    assert saved['decision']['reviewer'] == 'demo-analyst'
    assert saved['decision']['package_hash'] == case['package_hash']
    assert saved['analysis'] == case['analysis']
    assert len(saved['events']) == 3
    assert result['graph']['next_nodes'] == []
    assert not result['graph']['waiting_for_review']
    assert cli('show', cid) == result
    assert cli('resume', cid) == result
    assert 'already been recorded' in cli(*args, expected=2)
    opposite = list(args)
    opposite[3] = 'rejected' if choice == 'approved' else 'approved'
    assert 'already been recorded' in cli(*opposite, expected=2)
    assert cli('show', cid) == result


@pytest.mark.parametrize('changes', [
    {'expected_revision': 0}, {'package_hash': '0' * 64},
    {'reviewer': 'unlisted-reviewer'}, {'reason': '   '},
    {'reason': 'x' * 1001}, {'decision': 'override'},
    {'case_id': 'different-case'}, {'expected_revision': True},
])
def test_invalid_decision_does_not_advance_graph(tmp_path, changes):
    with open_graph(tmp_path / 'review.sqlite') as (store, graph):
        case = ready(store, graph)
        before = inspect_case(store, graph, case['case_id'])
        with pytest.raises(TransitionError):
            submit_decision(store, graph, case['case_id'], payload_for(case, **changes))
        assert inspect_case(store, graph, case['case_id']) == before


def test_unknown_payload_fields_rejected(tmp_path):
    with open_graph(tmp_path / 'review.sqlite') as (store, graph):
        case = ready(store, graph)
        with pytest.raises(TransitionError):
            submit_decision(store, graph, case['case_id'], payload_for(case, override_verification=True))


@pytest.mark.parametrize('verification', ['blocked', 'incomplete'])
def test_unverified_draft_is_held_and_cannot_be_approved(tmp_path, verification):
    draft = {'answer': 'UNVERIFIED_DRAFT_SENTINEL', 'claims': [{'value': 999}]}
    with open_graph(tmp_path / 'review.sqlite', lambda _: (draft, verification)) as (store, graph):
        case = ready(store, graph)
        assert case['workflow_status'] == 'held'
        assert case['analysis']['answer'] is None
        assert 'UNVERIFIED_DRAFT_SENTINEL' not in json.dumps(case)
        assert case['events'][-1]['reason_code'] == 'VERIFICATION_' + verification.upper()
        before = inspect_case(store, graph, case['case_id'])
        with pytest.raises(TransitionError):
            submit_decision(store, graph, case['case_id'], payload_for(case))
        assert inspect_case(store, graph, case['case_id']) == before


def test_queued_case_cannot_receive_decision(tmp_path):
    with open_graph(tmp_path / 'review.sqlite') as (store, graph):
        case = create_case(store, graph)['case']
        with pytest.raises(TransitionError):
            store.decide(case['case_id'], payload_for(case, package_hash='0' * 64))


def test_verification_gate_applies_even_if_workflow_status_is_wrong(tmp_path):
    with open_graph(tmp_path / 'review.sqlite', lambda _: ({}, 'blocked')) as (store, graph):
        case = ready(store, graph)
        case['workflow_status'] = 'awaiting_review'
        with closing(store.connect()) as db, db:
            db.execute('UPDATE review_cases SET body=? WHERE case_id=?',
                       (json.dumps(case), case['case_id']))
        with pytest.raises(TransitionError, match='passed verification'):
            store.decide(case['case_id'], payload_for(case))


@pytest.mark.parametrize('choice', ['approved', 'rejected'])
def test_restart_after_decision_commit_before_graph_checkpoint(tmp_path, choice):
    path = tmp_path / 'review.sqlite'
    with open_graph(path) as (store, graph):
        case = ready(store, graph)
        saved = store.decide(case['case_id'], payload_for(case, choice))
        # The case transaction committed, but the graph still holds its interrupt.
        assert inspect_case(store, graph, case['case_id'])['graph']['completion_recovery_needed']
    def forbidden(_):
        pytest.fail('Recovery must not rerun analysis')
    with open_graph(path, forbidden) as (store, graph):
        result = resume_case(store, graph, case['case_id'])
        assert result['case'] == saved
        assert result['graph']['next_nodes'] == []
        assert not result['graph']['completion_recovery_needed']
        assert resume_case(store, graph, case['case_id']) == result


def test_interrupted_analysis_hold_has_diagnostic_and_no_replay(tmp_path):
    path = tmp_path / 'review.sqlite'
    with open_graph(path) as (store, graph):
        cid = create_case(store, graph)['case']['case_id']
        store.transition(cid, 0, 'running')
    def forbidden(_):
        pytest.fail('Uncertain analysis must not replay')
    with open_graph(path, forbidden) as (store, graph):
        held = resume_case(store, graph, cid)
        assert held['case']['workflow_status'] == 'held'
        assert held['case']['events'][-1]['reason_code'] == 'INTERRUPTED_ANALYSIS_NO_RESULT'
        assert resume_case(store, graph, cid) == held


def test_competing_case_decisions_have_one_atomic_winner(tmp_path):
    path = tmp_path / 'review.sqlite'
    store = CaseStore(path)
    case = resume(store, store.create()['case_id'])
    barrier = Barrier(2)
    class RacingStore(CaseStore):
        def read(self, case_id):
            result = super().read(case_id)
            barrier.wait(timeout=5)
            return result
    def decide(choice):
        try:
            return RacingStore(path).decide(case['case_id'], payload_for(case, choice))['analyst_decision']
        except TransitionError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ['approved', 'rejected']))
    assert results.count('conflict') == 1
    saved = store.read(case['case_id'])
    assert saved['revision'] == 3
    assert len(saved['events']) == 3
    assert saved['analyst_decision'] in results


def test_tampered_recorded_decision_is_rejected(tmp_path):
    path = tmp_path / 'review.sqlite'
    with open_graph(path) as (store, graph):
        case = ready(store, graph)
        saved = submit_decision(store, graph, case['case_id'], payload_for(case))['case']
        saved['decision']['reason'] = 'Altered after recording'
        with closing(store.connect()) as db, db:
            db.execute('UPDATE review_cases SET body=? WHERE case_id=?',
                       (json.dumps(saved), case['case_id']))
        with pytest.raises(TransitionError, match='Decision integrity'):
            inspect_case(store, graph, case['case_id'])


def test_checkpoint_package_mismatch_rejected_before_decision(tmp_path):
    path = tmp_path / 'review.sqlite'
    with open_graph(path) as (store, graph):
        case = ready(store, graph)
        # A newly saved, valid package with the same input must not stand in for
        # the package that the persisted interrupt actually presented.
        from src.review_workflow import digest
        case['analysis']['answer'] = 'Replacement package'
        case['package_hash'] = digest([case['snapshot_hash'], case['analysis'], case['verification_status']])
        with closing(store.connect()) as db, db:
            db.execute('UPDATE review_cases SET body=? WHERE case_id=?',
                       (json.dumps(case), case['case_id']))
        with pytest.raises(TransitionError, match='Interrupted review package'):
            submit_decision(store, graph, case['case_id'], payload_for(case))
        assert store.read(case['case_id'])['analyst_decision'] == 'pending'
