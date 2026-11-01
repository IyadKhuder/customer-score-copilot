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
from langgraph.types import interrupt

from src.review_workflow import CaseStore, TransitionError, resume

GRAPH_VERSION = 'offline-review-graph-v1'


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


def build_graph(store, saver, analyzer=None):
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
        if case['verification_status'] != 'passed' or case['analysis'] is None:
            raise TransitionError('Only a verified saved package can await review')
        interrupt({'case_id': case['case_id'], 'package_hash': case['package_hash'],
                   'revision': case['revision'], 'scope': 'review package only',
                   'message': 'Decision submission is introduced in M1.2.'})
        raise TransitionError('Decision resume is not supported in M1.1')

    graph = StateGraph(GraphState)
    graph.add_node('analyze', analyze)
    graph.add_node('await_review', await_review)
    graph.add_edge(START, 'analyze')
    graph.add_conditional_edges('analyze', route, ['await_review', END])
    graph.add_edge('await_review', END)
    # Create saves input and stops before any analysis.
    return graph.compile(checkpointer=saver, interrupt_before=['analyze'])


@contextmanager
def open_graph(path, analyzer=None):
    store = CaseStore(path)
    with SqliteSaver.from_conn_string(str(Path(path).resolve())) as saver:
        # Only plain serializable graph fields; never enable pickle fallback.
        saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[])
        yield store, build_graph(store, saver, analyzer)


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
            'waiting_for_review': case['workflow_status'] == 'awaiting_review'}}


def resume_case(store, graph, case_id):
    view = inspect_case(store, graph, case_id)
    # Reading a held, failed, or awaiting-review case never executes the graph.
    # If the case write committed before the graph checkpoint, the analyze node
    # is safe to re-enter once: resume() returns saved analysis without rerun.
    if view['graph']['next_nodes'] == ['analyze']:
        graph.invoke(None, config(case_id), durability='sync')
    return inspect_case(store, graph, case_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    parser.add_argument('command', choices=['create', 'show', 'resume'])
    parser.add_argument('case_id', nargs='?')
    args = parser.parse_args()
    if args.command != 'create' and not args.case_id:
        parser.error('show/resume requires case_id')
    try:
        with open_graph(args.store) as (store, graph):
            result = create_case(store, graph) if args.command == 'create' else (
                inspect_case(store, graph, args.case_id) if args.command == 'show'
                else resume_case(store, graph, args.case_id))
    except (TransitionError, sqlite3.Error) as exc:
        parser.exit(2, f'Workflow error: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
