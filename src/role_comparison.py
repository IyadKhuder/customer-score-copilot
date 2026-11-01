"""M1.3 step 1: bounded scripted roles with the existing numeric publication gate.

No provider adapter, database, retrieval, persistent graph or analyst action.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.narrative_composer import CompositionError, compose_verified_score_bin
from src.structured_output import parse_structured_output
from src.verification_pipeline import verify_structured_response
from src.review_workflow import digest

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES = ROOT / 'evaluation' / 'role_comparison_scripted_v1.json'
EXPERIMENT = 'numeric-role-comparison-scripted-v1'
FINDING_CODES = {'missing_claim', 'unsupported_claim', 'context_mismatch', 'evidence_gap'}


def validate_evidence(evidence):
    if type(evidence) is not dict or set(evidence) != {'request', 'observations'}:
        raise ValueError('Invalid evidence contract')
    request, rows = evidence['request'], evidence['observations']
    if type(request) is not dict or set(request) != {'account_id', 'period', 'market'}:
        raise ValueError('Invalid request')
    if (type(request['account_id']) is not int or request['account_id'] <= 0
            or type(request['market']) is not int or request['market'] != 1):
        raise ValueError('This experiment requires one positive account ID and market 1')
    def month(value):
        if type(value) is not str:
            raise ValueError('Period must be a string')
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed.day != 1:
            raise ValueError('Period must be a canonical month start')
    month(request['period'])
    if type(rows) is not list or len(rows) > 12:
        raise ValueError('At most 12 frozen observations are allowed')
    seen_ids, seen_periods = set(), set()
    for row in rows:
        if type(row) is not dict or set(row) != {'evidence_id', 'period', 'score', 'bin', 'market'}:
            raise ValueError('Invalid observation')
        month(row['period'])
        if (type(row['evidence_id']) is not str or not row['evidence_id']
                or len(row['evidence_id']) > 80 or row['evidence_id'] in seen_ids
                or row['period'] in seen_periods or row['period'] > request['period']
                or type(row['score']) is not int or not 0 <= row['score'] <= 449
                or type(row['bin']) is not int or row['bin'] != 1 + row['score'] // 50
                or type(row['market']) is not int or row['market'] != request['market']):
            raise ValueError('Invalid or mismatched observation')
        seen_ids.add(row['evidence_id'])
        seen_periods.add(row['period'])
    return evidence


def validate_proposal(proposal):
    raw = json.dumps(proposal, allow_nan=False)
    if len(raw.encode()) > 16384:
        raise ValueError('Proposal exceeds 16 KiB')
    data = parse_structured_output(raw)
    if len(data['score_claims']) > 8 or data['policy_claims']:
        raise ValueError('Only up to eight numeric claims are supported in this experiment')
    return data


def validate_findings(response, evidence):
    if type(response) is not dict or set(response) != {'findings'}:
        raise ValueError('Reviewer may return findings only, not decisions or overrides')
    findings = response['findings']
    ids = {row['evidence_id'] for row in evidence['observations']}
    if type(findings) is not list or len(findings) > 6:
        raise ValueError('Reviewer is bounded to six findings')
    for finding in findings:
        if type(finding) is not dict or set(finding) != {'code', 'metric', 'evidence_ids'}:
            raise ValueError('Invalid reviewer finding')
        if (finding['code'] not in FINDING_CODES or finding['metric'] not in ('score', 'bin')
                or type(finding['evidence_ids']) is not list
                or len(finding['evidence_ids']) > 12
                or any(type(ref) is not str or ref not in ids for ref in finding['evidence_ids'])):
            raise ValueError('Unsupported finding or evidence reference')
    return deepcopy(findings)


class State(TypedDict, total=False):
    proposal: dict | None
    proposal_hash: str | None
    findings: list
    analysis_calls: int
    reviewer_calls: int
    analysis_error: bool
    reviewer_error: bool
    outcome: dict


def run_roles(evidence, analysis_role, reviewer_role=None, *, clock=perf_counter):
    """One analysis call and at most one reviewer call; no retries or repair loop.

    Roles receive independent copies. Only safe results leave this transient graph.
    """
    frozen = deepcopy(validate_evidence(evidence))
    snapshot_hash = digest(frozen)
    if not callable(analysis_role) or (reviewer_role is not None and not callable(reviewer_role)):
        raise ValueError('Roles must be callable')
    def analyze(state):
        role_input = deepcopy(frozen)
        try:
            proposal = validate_proposal(analysis_role(role_input))
            if digest(role_input) != snapshot_hash:
                raise ValueError('Analysis role modified its frozen input')
            return dict(proposal=proposal, proposal_hash=digest(proposal), analysis_calls=1)
        except Exception:
            return dict(proposal=None, proposal_hash=None, analysis_calls=1, analysis_error=True)

    def review(state):
        role_input, proposal_input = deepcopy(frozen), deepcopy(state['proposal'])
        try:
            response = reviewer_role(role_input, proposal_input)
            if digest(role_input) != snapshot_hash or digest(proposal_input) != state['proposal_hash']:
                raise ValueError('Reviewer modified its frozen inputs')
            findings = validate_findings(response, frozen)
            return dict(findings=findings, reviewer_calls=1)
        except Exception:
            return dict(findings=[], reviewer_calls=1, reviewer_error=True)

    def gate(state):
        reasons, answer, verification, composition = [], None, 'not_run', 'not_run'
        if state.get('analysis_error'):
            reasons.append('ANALYSIS_ROLE_INVALID_OR_FAILED')
        else:
            proposal = state['proposal']
            tools = [{'tool_name': 'get_history',
                      'arguments': {'account_id': frozen['request']['account_id']},
                      'result': deepcopy(frozen['observations'])}]
            try:
                audit = verify_structured_response(json.dumps(proposal), tools, narrative_mode='claims_first')
            except Exception:
                return {'outcome': dict(publishable=False, public_answer=None,
                        verification_status='unavailable', composition_status='not_run',
                        reason_codes=['DETERMINISTIC_GATE_UNAVAILABLE'])}
            verification = audit['status']
            if verification != 'passed':
                reasons.append('DETERMINISTIC_VERIFICATION_BLOCKED')
            else:
                try:
                    answer = compose_verified_score_bin(audit, account_id=frozen['request']['account_id'],
                                                         period=frozen['request']['period'])
                    composition = 'passed'
                except CompositionError:
                    composition = 'blocked'
                    reasons.append('DETERMINISTIC_COMPOSITION_BLOCKED')
            # Reviewer can add a hold, but can never erase deterministic reasons.
            if state.get('reviewer_error'):
                reasons.append('REVIEWER_INVALID_OR_FAILED')
            elif state.get('findings'):
                reasons.append('REVIEWER_REQUESTED_INSPECTION')
        return {'outcome': dict(publishable=not reasons, public_answer=answer if not reasons else None,
                                verification_status=verification, composition_status=composition,
                                reason_codes=reasons)}

    builder = StateGraph(State)
    builder.add_node('analysis_role', analyze)
    builder.add_node('publication_gate', gate)
    builder.add_edge(START, 'analysis_role')
    if reviewer_role is None:
        builder.add_edge('analysis_role', 'publication_gate')
    else:
        builder.add_node('evidence_review_role', review)
        builder.add_conditional_edges('analysis_role',
            lambda state: 'publication_gate' if state.get('analysis_error') else 'evidence_review_role',
            ['publication_gate', 'evidence_review_role'])
        builder.add_edge('evidence_review_role', 'publication_gate')
    builder.add_edge('publication_gate', END)
    graph = builder.compile()  # Isolated experiment; deliberately no checkpoints.
    started = clock()
    state = graph.invoke(dict(findings=[], reviewer_calls=0, analysis_error=False, reviewer_error=False),
                         {'recursion_limit': 8})
    elapsed_ms = max(0.0, (clock() - started) * 1000)
    return dict(state['outcome'], snapshot_hash=snapshot_hash,
                proposal_hash=state.get('proposal_hash'), reviewer_findings=state.get('findings', []),
                role_invocations={'analysis': state['analysis_calls'], 'reviewer': state['reviewer_calls']},
                elapsed_ms=elapsed_ms)


def load_cases(path=DEFAULT_FIXTURES):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if set(data) != {'experiment_id', 'description', 'cases'} or data['experiment_id'] != EXPERIMENT:
        raise ValueError('Unsupported comparison manifest')
    cases = data['cases']
    if type(cases) is not list or not 1 <= len(cases) <= 40:
        raise ValueError('Expected 1..40 scripted cases')
    ids = set()
    for case in cases:
        if set(case) != {'case_id', 'description', 'evidence', 'proposal', 'reviewer_response',
                         'reviewer_raises', 'expected_publishable'}:
            raise ValueError('Invalid scripted case fields')
        if type(case['case_id']) is not str or not case['case_id'] or case['case_id'] in ids:
            raise ValueError('Case IDs must be unique nonblank strings')
        if type(case['expected_publishable']) is not bool or type(case['reviewer_raises']) is not bool:
            raise ValueError('Expected outcomes and failure switches must be booleans')
        validate_evidence(case['evidence'])
        # Proposals/reviewer responses may intentionally be invalid fixtures.
        ids.add(case['case_id'])
    return data


def compare_scripted(path=DEFAULT_FIXTURES, *, repetitions=3):
    if type(repetitions) is not int or not 1 <= repetitions <= 10:
        raise ValueError('Repetitions must be 1..10')
    manifest = load_cases(path)
    records = []
    for case in manifest['cases']:
        def analyze(evidence):
            return deepcopy(case['proposal'])
        def review(evidence, proposal):
            if case['reviewer_raises']:
                raise RuntimeError('Scripted unavailability')
            return deepcopy(case['reviewer_response'])
        for mode in ['analysis_only', 'analysis_plus_review']:
            outputs = [run_roles(case['evidence'], analyze,
                                review if mode == 'analysis_plus_review' else None)
                       for _ in range(repetitions)]
            stable = [{key: value for key, value in output.items() if key != 'elapsed_ms'} for output in outputs]
            if any(output != stable[0] for output in stable):
                raise ValueError('Scripted outcome changed across repetitions')
            records.append(dict(case_id=case['case_id'], mode=mode,
                                expected_publishable=case['expected_publishable'],
                                **stable[0], median_elapsed_ms=median(o['elapsed_ms'] for o in outputs)))
    for index in range(0, len(records), 2):
        if records[index]['proposal_hash'] != records[index + 1]['proposal_hash']:
            raise ValueError('Paired modes did not use the same proposal')
    summary = {}
    for mode in ['analysis_only', 'analysis_plus_review']:
        rows = [row for row in records if row['mode'] == mode]
        summary[mode] = dict(cases=len(rows),
            expected_outcome_matches=sum(r['publishable'] == r['expected_publishable'] for r in rows),
            incorrect_publications=sum(r['publishable'] and not r['expected_publishable'] for r in rows),
            unnecessary_holds=sum(not r['publishable'] and r['expected_publishable'] for r in rows),
            median_case_latency_ms=median(r['median_elapsed_ms'] for r in rows),
            role_invocations_per_repetition={role: sum(r['role_invocations'][role] for r in rows)
                                             for role in ['analysis', 'reviewer']})
    code_files = ['role_comparison.py', 'verification_pipeline.py', 'structured_output.py',
                  'guardrail.py', 'narrative_composer.py']
    return dict(experiment_id=EXPERIMENT, execution_mode='scripted_offline',
                created_at=datetime.now(timezone.utc).isoformat(), fixture_hash=digest(manifest),
                code_hash=digest({name: (ROOT / 'src' / name).read_text() for name in code_files}),
                scope='numeric score/bin only; no policy/retrieval or LLM quality measurement',
                repetitions=repetitions,
                latency_scope='graph.invoke wall time; compilation excluded; lazy-import/warmup effects possible',
                workflow_executions=len(records) * repetitions,
                live_provider_requests=0, token_usage=None, estimated_live_cost=None,
                summary=summary, cases=records,
                interpretation='Scripted plumbing evidence only; keep baseline selectable. No model-quality conclusion.')
