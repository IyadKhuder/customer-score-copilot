"""Durable LangGraph adapter for the isolated, offline review workflow."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.review_workflow import (CaseStore, DEMO_REVIEWERS, TransitionError,
                                 resume, validate_decision)

GRAPH_VERSION = 'offline-review-graph-v2'


class GraphState(TypedDict):
    case_id: str
    snapshot_hash: str
    graph_version: str


def checked_case(store, state):
    if state.get('graph_version') != GRAPH_VERSION:
        raise TransitionError('Graph version mismatch; inspect before resuming')
    case = store.read(state['case_id'])
    if state['snapshot_hash'] != case['snapshot_hash']:
        raise TransitionError('Graph/case snapshot mismatch')
    return case


def build_graph(store, saver, analyzer=None, allowed_reviewers=DEMO_REVIEWERS):
    def analyze(state):
        checked_case(store, state)
        if analyzer is None:
            resume(store, state['case_id'])
        else:
            resume(store, state['case_id'], analyzer)
        # The case is the authority. Replaying this node reads saved output.
        return {}

    def route(state):
        case = checked_case(store, state)
        return 'await_review' if case['workflow_status'] == 'awaiting_review' else END

    def await_review(state):
        case = checked_case(store, state)
        if case['workflow_status'] == 'completed':
            # CaseStore.read already validates the durable decision. This branch
            # repairs a graph whose decision write committed before checkpointing.
            return {}
        if case['verification_status'] != 'passed' or case['analysis'] is None:
            raise TransitionError('Only a verified saved package can await review')
        payload = interrupt({'case_id': case['case_id'], 'package_hash': case['package_hash'],
                   'revision': case['revision'], 'scope': 'review package only',
                   'message': 'Approve or reject this exact saved review package.'})
        store.decide(case['case_id'], payload, allowed_reviewers)
        return {}

    graph = StateGraph(GraphState)
    graph.add_node('analyze', analyze)
    graph.add_node('await_review', await_review)
    graph.add_edge(START, 'analyze')
    graph.add_conditional_edges('analyze', route, ['await_review', END])
    graph.add_edge('await_review', END)
    # Create saves input and stops before any analysis.
    return graph.compile(checkpointer=saver, interrupt_before=['analyze'])


@contextmanager
def open_graph(path, analyzer=None, allowed_reviewers=DEMO_REVIEWERS):
    store = CaseStore(path)
    with SqliteSaver.from_conn_string(str(Path(path).resolve())) as saver:
        # Only plain serializable graph fields; never enable pickle fallback.
        saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[])
        yield store, build_graph(store, saver, analyzer, frozenset(allowed_reviewers))


def config(case_id):
    return {'configurable': {'thread_id': case_id}}


def create_case(store, graph):
    case = store.create()
    graph.invoke(dict(case_id=case['case_id'], snapshot_hash=case['snapshot_hash'],
                      graph_version=GRAPH_VERSION), config(case['case_id']), durability='sync')
    return inspect_case(store, graph, case['case_id'])


def inspect_case(store, graph, case_id):
    state = graph.get_state(config(case_id))
    if not state.values:
        raise TransitionError('No graph checkpoint for this case; use review_graph create')
    if state.values.get('case_id') != case_id:
        raise TransitionError('Graph/case identity mismatch')
    case = checked_case(store, state.values)
    return {'case': case, 'graph': {'version': GRAPH_VERSION,
            'next_nodes': list(state.next),
            'checkpoint_id': state.config['configurable']['checkpoint_id'],
            'waiting_for_review': case['workflow_status'] == 'awaiting_review',
            'completion_recovery_needed': case['workflow_status'] == 'completed' and bool(state.next)}}


def resume_case(store, graph, case_id):
    view = inspect_case(store, graph, case_id)
    # Reading a held, failed, or awaiting-review case never executes the graph.
    # If the case write committed before the graph checkpoint, the analyze node
    # is safe to re-enter once: resume() returns saved analysis without rerun.
    if view['graph']['next_nodes'] == ['analyze']:
        graph.invoke(None, config(case_id), durability='sync')
    elif view['graph']['completion_recovery_needed']:
        if view['graph']['next_nodes'] != ['await_review']:
            raise TransitionError('Unexpected graph position; inspect before recovery')
        graph.invoke(Command(resume={'recovery': 'use_saved_decision'}),
                     config(case_id), durability='sync')
    return inspect_case(store, graph, case_id)


def submit_decision(store, graph, case_id, payload, allowed_reviewers=DEMO_REVIEWERS):
    view = inspect_case(store, graph, case_id)
    validate_decision(view['case'], payload, allowed_reviewers)
    state = graph.get_state(config(case_id))
    pending = [item for task in state.tasks for item in task.interrupts]
    if list(state.next) != ['await_review'] or len(pending) != 1:
        raise TransitionError('Graph is not paused for a review decision')
    value = pending[0].value
    if (value.get('case_id') != case_id or value.get('revision') != payload['expected_revision']
            or value.get('package_hash') != payload['package_hash']):
        raise TransitionError('Interrupted review package does not match decision')
    graph.invoke(Command(resume=payload), config(case_id), durability='sync')
    return inspect_case(store, graph, case_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    parser.add_argument('command', choices=['create', 'show', 'resume', 'decide'])
    parser.add_argument('case_id', nargs='?')
    parser.add_argument('--decision', choices=['approved', 'rejected'])
    parser.add_argument('--reviewer', help='Offline declared identity; demo-analyst is allowed')
    parser.add_argument('--reason')
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--package-hash')
    args = parser.parse_args()
    if args.command != 'create' and not args.case_id:
        parser.error('show/resume/decide requires case_id')
    values = [args.decision, args.reviewer, args.reason, args.expected_revision, args.package_hash]
    if args.command == 'decide' and any(value is None for value in values):
        parser.error('decide requires decision, reviewer, reason, expected-revision and package-hash')
    if args.command != 'decide' and any(value is not None for value in values):
        parser.error('Decision options are only valid with decide')
    try:
        with open_graph(args.store) as (store, graph):
            if args.command == 'create':
                result = create_case(store, graph)
            elif args.command == 'show':
                result = inspect_case(store, graph, args.case_id)
            elif args.command == 'resume':
                result = resume_case(store, graph, args.case_id)
            else:
                payload = dict(case_id=args.case_id, decision=args.decision, reviewer=args.reviewer,
                               reason=args.reason, expected_revision=args.expected_revision,
                               package_hash=args.package_hash)
                result = submit_decision(store, graph, args.case_id, payload)
    except (TransitionError, sqlite3.Error) as exc:
        parser.exit(2, f'Workflow error: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
