"""6.5.1: read-only comparison; no agent imports or provider execution."""
from __future__ import annotations

import json
from src.evaluation_metrics import score_report

CAUTION = (
    'Descriptive engineering observations only. No causal improvement or provider '
    'superiority is established. Six synthetic cases include only one negative case. '
    'Policy-ID coverage does not measure semantic retrieval or factual accuracy. '
    'Case timings are not total experiment wall-clock time.'
)
CONDITION_FIELDS = ('policy_passages', 'policy_tool_available', 'code_revision',
                    'prompt_revision', 'pacing', 'model_configuration')


def compare_reports(entries, *, benchmark, fixtures):
    """entries: label/run/scores/condition dictionaries; inputs are never mutated.

    Recompute scores with the existing scorer and reject stale/tampered pairs.
    Annotations are caller-supplied descriptions, not authenticated provenance.
    """
    if not entries:
        raise ValueError('At least one report pair is required')
    labels = [e['label'] for e in entries]
    if any(not isinstance(x, str) or not x.strip() for x in labels) or len(set(labels)) != len(labels):
        raise ValueError('Labels must be unique nonblank strings')
    output = []
    for entry in entries:
        run = entry['run']
        fresh = score_report(run, benchmark=benchmark, fixtures=fixtures)
        # JSON comparison also distinguishes booleans from integers.
        if json.dumps(fresh, sort_keys=True, allow_nan=False) != json.dumps(entry['scores'], sort_keys=True, allow_nan=False):
            raise ValueError(f"{entry['label']}: saved score report differs from offline rescoring")
        condition = entry.get('condition', {})
        if not isinstance(condition, dict) or set(condition) - set(CONDITION_FIELDS):
            raise ValueError('Unsupported condition annotation')
        if any(not isinstance(v, str) or not v.strip() for v in condition.values()):
            raise ValueError('Condition annotations must be nonblank strings')
        rows = []
        for record, scored in zip(run['cases'], fresh['cases']):
            obs = record['observed']
            rows.append({
                'case_id': record['case_id'], 'passed': scored['passed'],
                'publication_match': scored['publication_match'],
                'decision': ('ERROR' if obs is None else 'PUBLISH' if obs['publishable'] else 'BLOCK'),
                'status': obs['status'] if obs else None,
                'reason_codes': obs['reason_codes'][:] if obs else [],
                'next_action': obs['next_action'] if obs else None,
                'retrieved_policy_ids': obs['retrieved_policy_ids'][:] if obs else [],
                'tool_names': obs['tool_names'][:] if obs else [],
                'tool_rounds': obs['tool_rounds'] if obs else None,
                'elapsed_ms': record['elapsed_ms'], 'error_type': record['error_type'],
            })
        output.append({'label': entry['label'], 'provider_id': run['provider_id'],
                       'execution_mode': run['execution_mode'],
                       'condition_annotations': {k: condition.get(k, 'unknown') for k in CONDITION_FIELDS},
                       'metrics': fresh['metrics'], 'cases': rows})
    flags = []
    for key in CONDITION_FIELDS:
        values = [r['condition_annotations'][key] for r in output]
        if 'unknown' in values:
            flags.append(f'{key}: incomplete provenance')
        if len(set(values)) > 1:
            flags.append(f'{key}: mixed conditions')
    if len({r['execution_mode'] for r in output}) > 1:
        flags.append('execution_mode: scripted and provider executions differ')
    return {'comparison_version': '6.5.1', 'causal_claims_supported': False,
            'comparability': 'not established', 'comparability_flags': flags,
            'caution': CAUTION, 'runs': output}


def render_markdown(result):
    """Render a compact overview plus auditable per-run case detail."""
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    lines = ['# Module 6.5.1 — Existing report comparison', '', result['caution'], '',
             'Comparability: **not established**. Condition annotations are supplied separately; they are not verified execution metadata.', '',
             '| Run | All-checks PASS | Publication accuracy | Policy-ID coverage | Errors | Mean case ms* |',
             '|---|---:|---:|---:|---:|---:|']
    for run in result['runs']:
        m = run['metrics']
        lines.append(f"| {cell(run['label'])} | {m['cases_passed_all_checks']}/{m['cases_total']} | {m['publication_decision_accuracy']:.1%} | {m['required_policy_retrieval_coverage']:.1%} | {m['execution_error_count']} | {m['mean_successful_run_elapsed_ms']} |")
    lines += ['', '*Mean excludes execution-error cases; includes recorded per-case overhead. Deliberate inter-case pauses are outside case timing.', '', 'Comparability flags:', '']
    lines += [f'- {cell(flag)}' for flag in result['comparability_flags']]
    for run in result['runs']:
        lines += ['', f"## {cell(run['label'])}", '', f"Provider label: {cell(run['provider_id'])}", '']
        lines += [f'- {key}: {cell(value)}' for key, value in run['condition_annotations'].items()]
        lines += ['', '| Case | Decision / status | PASS | Reasons / next action | Policy IDs | Tool calls (ordered) / rounds | ms | Error |', '|---|---|---|---|---|---|---:|---|']
        for r in run['cases']:
            values = [r['case_id'], f"{r['decision']} / {r['status']}", r['passed'], f"{','.join(r['reason_codes'])} / {r['next_action']}", ','.join(r['retrieved_policy_ids']), f"{','.join(r['tool_names'])} / {r['tool_rounds']}", r['elapsed_ms'], r['error_type']]
            lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
    return '\n'.join(lines) + '\n'
