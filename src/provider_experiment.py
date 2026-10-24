"""6.5.2.5: opt-in metadata-bound provider invocation. Serial standalone use only."""
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.experiment_metadata import build_experiment_metadata
from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import load_fixtures
from src.evaluation_metrics import score_report


def _encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _current(plan, root):
    if (plan.get('metadata_version') != '6.5.2.2' or plan.get('record_kind') != 'preflight_plan'
            or plan.get('execution_verified') is not False or plan.get('execution_record') is not None):
        raise ValueError('An unexecuted 6.5.2.2 plan is required')
    s = plan['planned_settings']
    fresh = build_experiment_metadata(
        root, provider=plan['provider_configuration']['provider'],
        policy_passages=s['policy_passage_variant'], max_tool_rounds=s['max_tool_rounds'],
        between_case_seconds=s['between_case_seconds'], retry_delay_seconds=s['retry_delay_seconds'],
        max_rate_limit_retries=s['max_rate_limit_retries'])
    clean = lambda p: {k: v for k, v in p.items() if k != 'created_at_utc'}
    if clean(plan) != clean(fresh):
        raise ValueError('Plan is stale or incompatible; capture a fresh plan')
    return fresh


def run_provider_experiment(plan, root, destination, *, confirm_live_api=False,
                            client_factory=None, sleeper=None, clock=None, request_budget=None):
    """Run actual paced/helper/agent/verifier path; require explicit authorization.

    Default factory builds the configured SDK adapter. Tests inject fake-SDK
    adapters and time hooks. Metadata distinguishes this injection and never
    claims provider-authenticated model identity or measured API cost.
    """
    if confirm_live_api is not True:
        raise ValueError('Explicit confirm_live_api=True is required before provider access')
    root = Path(root).resolve()
    # Prevent capturing one tree while executing src from another checkout.
    if Path(__file__).resolve().parent.parent != root:
        raise ValueError('Project root must match the imported source tree')
    plan = copy.deepcopy(plan)
    _current(plan, root)
    benchmark = load_benchmark(root / 'evaluation/benchmark_cases_synthetic_v2.json')
    fixtures = load_fixtures(root / 'evaluation/scenario_fixtures_synthetic_v2.json', benchmark=benchmark)
    destination = Path(destination)
    # Reserve output before constructing a client or spending quota. Parent must exist.
    destination.mkdir()
    with (destination / 'plan.json').open('xb') as f:
        f.write(_encoded(plan))
    from src import paced_assisted_benchmark as paced
    from src.llm_client import get_llm_client, OpenAICompatibleClient, AnthropicClient
    injected = client_factory is not None
    factory = get_llm_client if client_factory is None else client_factory
    config = plan['provider_configuration']
    adapter = factory(config['provider'])
    expected_type = AnthropicClient if config['adapter'] == 'anthropic' else OpenAICompatibleClient
    if type(adapter) is not expected_type or adapter.model != config['configured_model']:
        raise ValueError('Constructed adapter class/model differs from plan')
    configured_sdk = None
    if request_budget is not None:
        if request_budget.started or request_budget.limit < 1:
            raise ValueError('A fresh budget with available requests is required')
        configured_sdk = request_budget.wrap_adapter(adapter, config['adapter'])
    # Imports/factory must not have changed fingerprinted source files.
    _current(plan, root)
    attempts = Counter()
    exceptions = {}
    actual_case = paced.run_live_case

    def tracked_case(case, fixture, client, **kwargs):
        cid = case['id']
        attempts[cid] += 1
        try:
            return actual_case(case, fixture, client, **kwargs)
        except Exception as exc:
            exceptions.setdefault(cid, []).append(type(exc).__name__)
            raise

    s = plan['planned_settings']
    hooks = {}
    if sleeper is not None: hooks['sleeper'] = sleeper
    if clock is not None: hooks['clock'] = clock
    started = datetime.now(timezone.utc).isoformat()
    label = config['provider'] + ('.injected_assisted_bound' if injected else '.live_assisted_bound')
    if request_budget is not None:
        request_budget.attach_log(destination / 'requests.jsonl')
    try:
        with patch.object(paced, 'run_live_case', tracked_case):
            report = paced.run_paced_assisted_benchmark(
                adapter, provider_id=label, policy_passage_variant=s['policy_passage_variant'],
                max_tool_rounds=s['max_tool_rounds'], between_case_seconds=s['between_case_seconds'],
                retry_delay_seconds=s['retry_delay_seconds'], max_rate_limit_retries=s['max_rate_limit_retries'],
                benchmark=benchmark, fixtures=fixtures, **hooks)
    finally:
        if request_budget is not None:
            request_budget.close()
    ended = datetime.now(timezone.utc).isoformat()
    scores = score_report(report, benchmark=benchmark, fixtures=fixtures)
    try:
        _current(plan, root)
        stable = True
    except (ValueError, OSError, SyntaxError):
        stable = False
    raw_report, raw_scores = _encoded(report), _encoded(scores)
    metadata = {
        'metadata_version': '6.5.2.5',
        'record_kind': 'injected_adapter_execution' if injected else 'configured_provider_execution',
        'execution_verified': False,
        'live_provider_execution_verified': False,
        'plan': plan,
        'execution_record': {
            'runner_invocation_completed': True, 'started_at_utc': started, 'finished_at_utc': ended,
            'source_snapshot_unchanged_after_run': stable,
            'client_factory_injected': injected,
            'time_hooks_injected': bool(hooks),
            'report_file': 'observations.json', 'report_sha256': _sha(raw_report),
            'scores_file': 'scores.json', 'scores_sha256': _sha(raw_scores),
            'case_attempt_counts': {r['case_id']: attempts[r['case_id']] for r in report['cases']},
            'case_retry_counts': {r['case_id']: max(0, attempts[r['case_id']] - 1) for r in report['cases']},
            'case_exception_types': {r['case_id']: exceptions.get(r['case_id'], []) for r in report['cases']},
            'served_model': None, 'api_cost': None,
            'limits': ['Application-level invocation record, not authenticated provider provenance.',
                       'Case retries exclude SDK internal retries. Zero attempts means failure before case entry.',
                       'Model identity, equal generation settings and SDK schema acceptance are not established by this record.'],
        },
    }
    if request_budget is not None:
        metadata['execution_record']['request_budget'] = request_budget.summary()
        metadata['execution_record']['request_log_file'] = 'requests.jsonl'
        metadata['execution_record']['request_log_sha256'] = _sha((destination / 'requests.jsonl').read_bytes())
    # A write failure leaves a clearly incomplete, new bundle; never delete evidence.
    for name, raw in [('observations.json', raw_report), ('scores.json', raw_scores),
                      ('metadata.json', _encoded(metadata))]:
        with (destination / name).open('xb') as f:
            f.write(raw)
    return report, scores, metadata
