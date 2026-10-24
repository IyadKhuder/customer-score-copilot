"""6.5.2.3: bind a preflight plan to an invocation and its validated report.

This increment exposes only an injected-runner offline rehearsal. It cannot
certify provider execution. Existing live runners and report schemas are unchanged.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src.experiment_metadata import build_experiment_metadata
from src.evaluation_dataset import load_benchmark
from src.evaluation_runner import load_fixtures
from src.evaluation_metrics import validate_run_report


def _bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def rehearse_bound_experiment(plan, root, client, *, runner):
    """Call an injected offline runner with settings taken solely from the plan.

    Caller must inject a no-network runner. This function does not construct a
    provider or authenticate execution. Never label its metadata as a live run.
    """
    root = Path(root).resolve()
    if not callable(runner):
        raise TypeError('An offline runner is required')
    original = copy.deepcopy(plan)
    if (plan.get('metadata_version') != '6.5.2.2'
            or plan.get('record_kind') != 'preflight_plan'
            or plan.get('execution_verified') is not False
            or plan.get('execution_record') is not None):
        raise ValueError('Expected an unexecuted 6.5.2.2 plan')
    settings = plan['planned_settings']
    config = plan['provider_configuration']
    options = dict(provider=config['provider'],
                   policy_passages=settings['policy_passage_variant'],
                   max_tool_rounds=settings['max_tool_rounds'],
                   between_case_seconds=settings['between_case_seconds'],
                   retry_delay_seconds=settings['retry_delay_seconds'],
                   max_rate_limit_retries=settings['max_rate_limit_retries'])
    fresh = build_experiment_metadata(root, **options)
    # All fields except the capture timestamp must match a fresh plan.
    comparable = lambda p: {k: v for k, v in p.items() if k != 'created_at_utc'}
    if comparable(fresh) != comparable(plan):
        raise ValueError('Plan differs from current sources, runtime, configuration or supported settings')
    if getattr(client, 'model', None) != config['configured_model']:
        raise ValueError('Client model differs from configured model')
    benchmark = load_benchmark(root / 'evaluation/benchmark_cases_synthetic_v2.json')
    fixtures = load_fixtures(root / 'evaluation/scenario_fixtures_synthetic_v2.json', benchmark=benchmark)
    label = config['provider'] + '.bound_offline_rehearsal'
    started = datetime.now(timezone.utc).isoformat()
    report = runner(client, provider_id=label,
                    policy_passage_variant=settings['policy_passage_variant'],
                    max_tool_rounds=settings['max_tool_rounds'],
                    between_case_seconds=settings['between_case_seconds'],
                    retry_delay_seconds=settings['retry_delay_seconds'],
                    max_rate_limit_retries=settings['max_rate_limit_retries'],
                    benchmark=copy.deepcopy(benchmark), fixtures=copy.deepcopy(fixtures))
    validate_run_report(report, benchmark, fixtures)
    if report['provider_id'] != label or report['execution_mode'] != 'provider_adapter':
        raise ValueError('Runner report identity or mode differs from invocation')
    ended = datetime.now(timezone.utc).isoformat()
    # Preserve useful outcomes when sources change during execution; never claim stability.
    try:
        after = build_experiment_metadata(root, **options)
        stable = comparable(after) == comparable(fresh)
    except (ValueError, OSError, SyntaxError):
        stable = False
    metadata = {
        'metadata_version': '6.5.2.3', 'record_kind': 'offline_runner_rehearsal',
        'execution_verified': False, 'live_provider_execution_verified': False,
        'plan': original,
        'execution_record': {
            'started_at_utc': started, 'finished_at_utc': ended,
            'runner_returned_valid_report': True,
            'source_snapshot_unchanged_after_run': stable,
            'report_file': 'observations.json',
            'report_sha256': _hash(_bytes(report)),
            'actual_retries': None, 'served_model': None,
            'limits': 'Injected offline runner; no claim about real SDK requests, policy correctness, or live model performance.',
        },
    }
    return copy.deepcopy(report), metadata


def save_bound_rehearsal(report, metadata, destination):
    """Create a NEW bundle directory. Refuse stale binding or existing directory.

    Exclusive writes; a disk failure may leave an incomplete bundle for inspection.
    No existing reports or plans are overwritten or deleted.
    """
    raw = _bytes(report)
    if (metadata.get('record_kind') != 'offline_runner_rehearsal'
            or metadata.get('execution_record', {}).get('report_sha256') != _hash(raw)
            or metadata['execution_record'].get('report_file') != 'observations.json'):
        raise ValueError('Report does not match metadata binding')
    meta_raw = _bytes(metadata)
    path = Path(destination)
    path.mkdir()  # atomic exclusive directory reservation; parent must exist
    with (path / 'observations.json').open('xb') as f:
        f.write(raw)
    with (path / 'metadata.json').open('xb') as f:
        f.write(meta_raw)
    return path
