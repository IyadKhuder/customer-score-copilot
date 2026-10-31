"""Persistence boundaries and forbidden transitions for the isolated skeleton."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from src.review_workflow import CaseStore, TransitionError, resume


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'workflow.sqlite'
        self.store = CaseStore(self.path)
        self.case = self.store.create()

    def test_process_restart_and_saved_result(self):
        def cli(command):
            output = subprocess.check_output([sys.executable, '-m', 'src.review_workflow',
                '--store', str(self.path), command, self.case['case_id']], text=True)
            return json.loads(output)
        self.assertEqual(cli('show')['workflow_status'], 'queued')
        first = cli('resume')
        self.assertEqual(first['workflow_status'], 'awaiting_review')
        self.assertEqual(first['verification_status'], 'passed')
        self.assertEqual(first['analyst_decision'], 'pending')
        self.assertEqual(first, cli('resume'))
        def forbidden(_):
            self.fail('Saved analysis must not rerun')
        self.assertEqual(resume(CaseStore(self.path), first['case_id'], forbidden), first)

    def test_invalid_and_stale_transitions(self):
        cid = self.case['case_id']
        with self.assertRaises(TransitionError):
            self.store.transition(cid, 0, 'awaiting_review')
        self.store.transition(cid, 0, 'running')
        with self.assertRaises(TransitionError):
            self.store.transition(cid, 0, 'held')
        with self.assertRaises(TransitionError):
            self.store.transition(cid, 1, 'awaiting_review', analysis={}, verification='blocked')
        with self.assertRaises(TransitionError):
            self.store.transition(cid, 1, 'completed')

    def test_interrupted_running_is_held(self):
        cid = self.case['case_id']
        self.store.transition(cid, 0, 'running')
        def forbidden(_):
            self.fail('Interrupted analysis must not replay automatically')
        held = resume(CaseStore(self.path), cid, forbidden)
        self.assertEqual(held['workflow_status'], 'held')
        self.assertEqual(held['verification_status'], 'not_requested')

    def test_blocked_analysis_cannot_advance(self):
        case = resume(self.store, self.case['case_id'], lambda _: ({'claims': []}, 'blocked'))
        self.assertEqual(case['workflow_status'], 'held')
        with self.assertRaises(TransitionError):
            self.store.transition(case['case_id'], case['revision'], 'awaiting_review',
                                  analysis={}, verification='passed')

    def test_analysis_failure_persisted(self):
        def fail(_):
            raise RuntimeError('demo failure')
        result = resume(self.store, self.case['case_id'], fail)
        self.assertEqual(CaseStore(self.path).read(result['case_id'])['workflow_status'], 'failed')

    def test_tampered_evidence_rejected(self):
        with closing(self.store.connect()) as db, db:
            body = dict(self.case, evidence={'scores': [999]})
            db.execute('UPDATE review_cases SET body=?', (json.dumps(body),))
        with self.assertRaises(TransitionError):
            self.store.read(self.case['case_id'])
