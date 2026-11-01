"""Exercise durable checkpoints, process boundaries and recovery authority."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.review_graph import (create_case, inspect_case, open_graph, resume_case)
from src.review_workflow import TransitionError, resume


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'graph.sqlite'

    def test_separate_processes_and_stable_review_checkpoint(self):
        def cli(command, cid=None):
            args = [sys.executable, '-m', 'src.review_graph', '--store', str(self.path), command]
            return json.loads(subprocess.check_output(args + ([cid] if cid else []), text=True))
        created = cli('create')
        cid = created['case']['case_id']
        self.assertEqual(created['case']['workflow_status'], 'queued')
        self.assertEqual(created['graph']['next_nodes'], ['analyze'])
        self.assertEqual(cli('show', cid), created)
        reviewed = cli('resume', cid)
        self.assertEqual(reviewed['case']['workflow_status'], 'awaiting_review')
        self.assertEqual(reviewed['graph']['next_nodes'], ['await_review'])
        self.assertEqual(reviewed['case']['analyst_decision'], 'pending')
        self.assertEqual(cli('resume', cid), reviewed)
        self.assertEqual(cli('show', cid), reviewed)

    def test_saved_analysis_before_checkpoint_is_not_recomputed(self):
        with open_graph(self.path) as (store, graph):
            cid = create_case(store, graph)['case']['case_id']
            saved = resume(store, cid)  # Simulate case commit before graph checkpoint.
        def forbidden(_):
            self.fail('Already saved analysis was recomputed')
        with open_graph(self.path, forbidden) as (store, graph):
            result = resume_case(store, graph, cid)
            self.assertEqual(result['case'], saved)
            self.assertTrue(result['graph']['waiting_for_review'])

    def test_running_without_output_is_held(self):
        with open_graph(self.path) as (store, graph):
            cid = create_case(store, graph)['case']['case_id']
            store.transition(cid, 0, 'running')
        def forbidden(_):
            self.fail('Uncertain attempt replayed')
        with open_graph(self.path, forbidden) as (store, graph):
            result = resume_case(store, graph, cid)
            self.assertEqual(result['case']['workflow_status'], 'held')
            self.assertEqual(result['graph']['next_nodes'], [])

    def test_blocked_analysis_ends_without_review_pause(self):
        with open_graph(self.path, lambda _: ({'claims': []}, 'blocked')) as (store, graph):
            cid = create_case(store, graph)['case']['case_id']
            result = resume_case(store, graph, cid)
            self.assertEqual(result['case']['verification_status'], 'blocked')
            self.assertEqual(result['case']['workflow_status'], 'held')
            self.assertEqual(result['graph']['next_nodes'], [])

    def test_analysis_failure_is_saved(self):
        def fail(_):
            raise RuntimeError('offline failure')
        with open_graph(self.path, fail) as (store, graph):
            cid = create_case(store, graph)['case']['case_id']
            self.assertEqual(resume_case(store, graph, cid)['case']['workflow_status'], 'failed')

    def test_missing_graph_checkpoint_rejected(self):
        with open_graph(self.path) as (store, graph):
            cid = store.create()['case_id']
            with self.assertRaisesRegex(TransitionError, 'No graph checkpoint'):
                inspect_case(store, graph, cid)

    def test_checkpoint_provenance_mismatch_rejected(self):
        with open_graph(self.path) as (store, graph):
            cid = create_case(store, graph)['case']['case_id']
            from src.review_graph import config
            graph.update_state(config(cid), {'snapshot_hash': 'different-snapshot'})
            with self.assertRaisesRegex(TransitionError, 'snapshot mismatch'):
                resume_case(store, graph, cid)

    def test_unknown_graph_version_rejected(self):
        with open_graph(self.path) as (store, graph):
            cid = create_case(store, graph)['case']['case_id']
            from src.review_graph import config
            graph.update_state(config(cid), {'graph_version': 'unknown-version'})
            with self.assertRaisesRegex(TransitionError, 'version mismatch'):
                resume_case(store, graph, cid)
