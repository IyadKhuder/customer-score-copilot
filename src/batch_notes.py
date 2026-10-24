"""Optional serial notes over a frozen scan. Existing agent and gate own publication."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from src.batch_scan import ROOT, _classify, _base, _sha, validate_request
from src.request_budget import RequestBudget, RequestBudgetExceeded

SCOPES = {
    'declines': 'score_bin_warning_signal_decline_events',
    'zero_declines': 'score_bin_warning_signal_no_declines',
    'quality_hold': 'score_bin_warning_signal_quality_hold',
}


def _validate_scan(report, root):
    """Refuse changed calculations or policy bytes before any provider request."""
    request = validate_request(report['request'])
    if report.get('run_status') not in ('completed', 'completed_with_errors'):
        raise ValueError('A completed scan is required')
    results = report['results']
    if [r['account_id'] for r in results] != request['account_ids']:
        raise ValueError('Scan account set/order mismatch')
    for policy, expected in report['source']['policy_sha256'].items():
        paths = list((root / 'policy_memos').glob(policy + '_*.md'))
        if len(paths) != 1 or hashlib.sha256(paths[0].read_bytes()).hexdigest() != expected:
            raise ValueError('Policy snapshot changed; rescan before generating notes')
    if set(report['source']['policy_sha256']) != {'POL-002','POL-004','POL-005','POL-007'}:
        raise ValueError('Missing policy provenance')
    for result in results:
        if result['narrative']['status'] != 'not_requested':
            raise ValueError('Use the original scan; do not overwrite previous note outcomes')
        if result['scan_outcome'] != 'attention_required':
            continue
        if any(row['period'][:7] > request['as_of_period'][:7] for row in result['scan_evidence']):
            raise ValueError('Future evidence is outside the scan snapshot')
        fresh = _base(result['account_id'], request['as_of_period'])
        _classify(fresh, copy.deepcopy(result['scan_evidence']), False)
        for key in ('market','as_of_period','scan_outcome','policy_decision','coverage_complete',
                    'missing_baseline_months','reason_codes','next_action'):
            if result[key] != fresh[key]:
                raise ValueError('Saved scan disagrees with its evidence')
    return request


def _tools(rows, account, period, policy_root):
    from src.score_tools import TOOL_SCHEMAS
    from src.retriever import RETRIEVER_TOOL_SCHEMA, load_all_chunks, build_index, retrieve
    from src.policy_assist_suite import SUITE_TOOL_SCHEMA, build_suite_assist_dispatch
    frozen = copy.deepcopy(rows)
    passages = []
    chunks = load_all_chunks(str(policy_root / 'policy_memos'))
    vectorizer, matrix = build_index(chunks)

    def history(*, account_id):
        if type(account_id) is not int or account_id != account:
            raise ValueError('Account outside bound snapshot')
        return copy.deepcopy(frozen)

    def policies(*, query, k=3):
        if type(query) is not str or not query.strip() or len(query) > 2000:
            raise ValueError('Invalid policy query')
        if type(k) is not int or not 1 <= k <= 7:
            raise ValueError('Policy k must be 1..7')
        found = retrieve(query, chunks, vectorizer, matrix, k)
        passages.extend(copy.deepcopy(found))
        return found

    assist = build_suite_assist_dispatch(frozen, account_id=account,
                                        as_of_period=period, market=1)
    schemas = [copy.deepcopy(next(t for t in TOOL_SCHEMAS if t['name']=='get_history')),
               copy.deepcopy(RETRIEVER_TOOL_SCHEMA), copy.deepcopy(SUITE_TOOL_SCHEMA)]
    return schemas, dict(get_history=history, retrieve_policy=policies,
                         evaluate_policy_signals=assist), passages


def add_verified_notes(report, client, budget, *, adapter_kind, max_notes=2,
                       max_tool_rounds=4, source_root=ROOT):
    """Copy a scan, attempt at most 5 notes, retain all deterministic scan outcomes.

    Client must be a fresh project adapter; wrap its actual SDK request method,
    with retries disabled. The caller supplies a fresh RequestBudget and owns logs.
    No raw provider draft or private verification audit is persisted.
    """
    if type(max_notes) is not int or not 1 <= max_notes <= 5:
        raise ValueError('max_notes must be 1..5')
    if type(max_tool_rounds) is not int or not 1 <= max_tool_rounds <= 8:
        raise ValueError('max_tool_rounds must be 1..8')
    if not isinstance(budget, RequestBudget) or budget.started:
        raise ValueError('A fresh RequestBudget is required')
    if adapter_kind not in ('openai_compatible','anthropic'):
        raise ValueError('Unknown adapter kind')
    request = _validate_scan(report, Path(source_root))
    from src import agent
    from src.policy_assist_suite import SUITE_PROMPT_APPENDICES
    out = copy.deepcopy(report)
    original_client = client.client
    budget.wrap_adapter(client, adapter_kind)
    attempted = 0
    try:
        for result in out['results']:
            if result['scan_outcome'] != 'attention_required':
                continue
            note = result['narrative']
            if attempted >= max_notes:
                note.update(reason_codes=['NOTE_LIMIT_REACHED'], next_action='run_separate_note_batch')
                continue
            if budget.started >= budget.limit:
                note.update(reason_codes=['REQUEST_BUDGET_EXHAUSTED'], next_action='provide_request_allowance')
                continue
            rows = result['scan_evidence']
            if any(r['period'][8:] != '01' for r in rows):
                note.update(status='blocked', reason_codes=['NOTE_DATE_FORMAT_UNSUPPORTED'],
                            next_action='manual_review')
                continue
            d = result['policy_decision']
            count = sum(e['criterion']=='consecutive_declines' for e in d['events'])
            scope = SCOPES['quality_hold' if d['data_quality_flags'] else
                           'declines' if count else 'zero_declines']
            attempted += 1
            before = budget.started
            passages = []
            note.update(claim_scope=scope, evidence_sha256=_sha(rows),
                        scope_description='Verified score, bin, warning status and consecutive-decline events only; consult scan for other review indications.')
            try:
                schemas, dispatch, passages = _tools(rows, result['account_id'], request['as_of_period'], Path(source_root))
                question = (
                    f"Produce a verified case note for account {result['account_id']} at "
                    f"{request['as_of_period']}, market 1. Use get_history, evaluate_policy_signals "
                    "and retrieve_policy. Retrieve early-warning, consecutive-decline and data-quality policy evidence. "
                    "Report only this claim scope: " + scope + '. '
                    "Other scan review indications remain in the scan report, outside this note. "
                    "Do not infer causal reasons or authorize risk actions.\n" + SUITE_PROMPT_APPENDICES[scope]
                )
                response = agent.run_agent(question, client, max_tool_rounds=max_tool_rounds,
                    verbose=False, publication_mode='claims_first',
                    expected_account_id=result['account_id'], expected_period=request['as_of_period'],
                    expected_market=1, claim_scope=scope, tool_schemas=schemas, tool_dispatch=dispatch)
                # Publication also requires an actual policy retrieval in this run.
                if response['publishable'] and not passages:
                    note.update(status='blocked', publishable=False, answer=None,
                                reason_codes=['POLICY_RETRIEVAL_MISSING'], next_action='retrieve_policy_evidence')
                else:
                    note.update(status='published' if response['publishable'] else 'blocked',
                                publishable=response['publishable'],
                                answer=response['answer'] if response['publishable'] else None,
                                reason_codes=copy.deepcopy(response['reason_codes']),
                                next_action=response['next_action'])
            except RequestBudgetExceeded:
                note.update(status='failed', reason_codes=['REQUEST_BUDGET_EXHAUSTED'],
                            next_action='provide_request_allowance')
            except Exception as exc:
                note.update(status='failed', reason_codes=['NOTE_EXECUTION_FAILED'],
                            next_action='inspect_error', error_type=type(exc).__name__)
            finally:
                note['requests_started'] = budget.started - before
                note['policy_passages'] = passages
    finally:
        client.client = original_client
    out['notes'] = dict(note_run_id=str(uuid4()), created_at_utc=datetime.now(timezone.utc).isoformat(),
        scan_input_sha256=report['source'].get('input_sha256'), policy_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((Path(source_root) / 'policy_memos').glob('POL-*.md'))},
        max_notes=max_notes, attempted_count=attempted,
        outcomes={s:sum(r['narrative']['status']==s for r in out['results'])
                  for s in ('published','blocked','failed','not_requested')},
        request_budget=budget.summary(),
        retrieval='TF-IDF over actual policy_memos; passage IDs do not establish semantic relevance',
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        agent_sha256=hashlib.sha256(Path(agent.__file__).read_bytes()).hexdigest())
    return out
