"""Offline preflight metadata. Does not import config, agent, SDKs, or run models."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import platform
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = (
    'src/config.py', 'src/agent.py', 'src/llm_client.py',
    'src/live_evaluation.py', 'src/paced_assisted_benchmark.py',
    'src/policy_assist_suite.py', 'src/policy_passage_variants.py',
    'scripts/run_paced_assisted_benchmark_646.py',
    'evaluation/benchmark_cases_synthetic_v2.json', 'evaluation/scenario_fixtures_synthetic_v2.json',
)
PROVIDERS = {'gemini': 'openai_compatible', 'anthropic': 'anthropic'}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _read(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError(f'Project file escapes root: {relative}')
    return path.read_bytes()


def _provider_config(raw, provider):
    # Parse only a literal PROVIDERS assignment: never execute load_dotenv or read keys.
    tree = ast.parse(raw.decode('utf-8-sig'))
    assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'PROVIDERS' for t in n.targets)]
    if len(assignments) != 1:
        raise ValueError('Expected one literal PROVIDERS assignment')
    try:
        config = ast.literal_eval(assignments[0].value)[provider]
    except (ValueError, TypeError, KeyError, SyntaxError) as exc:
        raise ValueError('Provider configuration must be a literal mapping') from exc
    if not isinstance(config, dict) or config.get('adapter') != PROVIDERS[provider]:
        raise ValueError('Unexpected adapter for provider')
    # Allowlist outputs; never serialize config wholesale, base URLs, or key fields.
    model = config.get('model')
    if not isinstance(model, str) or not model.strip() or len(model) > 150:
        raise ValueError('Configured model must be a nonblank string up to 150 characters')
    return {'provider': provider, 'adapter': config['adapter'], 'configured_model': model,
            'served_model': None, 'model_identity_verified': False}


def build_experiment_metadata(root, *, provider, policy_passages='clarified_v1',
                              max_tool_rounds=4, between_case_seconds=65,
                              retry_delay_seconds=65, max_rate_limit_retries=1):
    """Describe a proposed paced assisted run, never assert that it executed.

    Hash raw source bytes (line-ending sensitive) and canonical evaluation JSON.
    Include all immediate src/*.py and scripts/*.py, so prompt/helper/verifier
    dependencies present there are covered without requiring old patch versions.
    """
    root = Path(root).resolve()
    if provider not in PROVIDERS:
        raise ValueError('Unsupported provider')
    if policy_passages not in ('baseline', 'clarified_v1'):
        raise ValueError('Unsupported policy passages')
    if type(max_tool_rounds) is not int or not 1 <= max_tool_rounds <= 8:
        raise ValueError('Invalid tool-round limit')
    if type(max_rate_limit_retries) is not int or not 0 <= max_rate_limit_retries <= 3:
        raise ValueError('Invalid retry count')
    for delay in (between_case_seconds, retry_delay_seconds):
        if type(delay) not in (int, float) or not math.isfinite(delay) or not 0 <= delay <= 600:
            raise ValueError('Invalid pacing delay')
    # Require the complete local entry path before creating a plan.
    required_bytes = {p: _read(root, p) for p in REQUIRED}
    config = _provider_config(required_bytes['src/config.py'], provider)
    paths = sorted(set(REQUIRED) | {
        p.relative_to(root).as_posix() for folder in ('src', 'scripts')
        for p in (root / folder).glob('*.py') if p.is_file()
    })
    source_hashes = {p: _hash(required_bytes[p] if p in required_bytes else _read(root, p))
                     for p in paths}
    inputs = {}
    for key, path in [('benchmark', 'evaluation/benchmark_cases_synthetic_v2.json'),
                      ('fixtures', 'evaluation/scenario_fixtures_synthetic_v2.json')]:
        inputs[key + '_sha256'] = _hash(_canonical(json.loads(required_bytes[path])))
    return {
        'metadata_version': '6.5.2.2',
        'record_kind': 'preflight_plan',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'execution_verified': False,
        'provider_configuration': config,
        'planned_settings': {
            'entrypoint': 'scripts.run_paced_assisted_benchmark_646',
            'policy_passage_variant': policy_passages, 'policy_tool_suite': True,
            'max_tool_rounds': max_tool_rounds,
            'between_case_seconds': float(between_case_seconds),
            'retry_delay_seconds': float(retry_delay_seconds),
            'max_rate_limit_retries': max_rate_limit_retries,
            'temperature': {'state': 'not_controlled_by_this_plan', 'value': None},
            'output_token_budget': {'state': 'adapter_defined_not_captured', 'value': None},
            'sdk_retries_and_timeouts': 'not_captured',
        },
        'evaluation_fingerprints': inputs,
        'source_files_sha256': source_hashes,
        'source_snapshot_sha256': _hash(_canonical(source_hashes)),
        'runtime': {'python_version': platform.python_version()},
        'execution_record': None,
        'limits': [
            'Plan only: not wired to live runner; settings must be supplied separately.',
            'No observation report binding, actual retries, served model, tokens, or costs recorded.',
            'Source files were read sequentially; keep the project idle during capture.',
            'Source coverage: immediate src/*.py and scripts/*.py plus required evaluation JSON; external dependencies are excluded.',
            'Matching fingerprints do not establish authentic execution or causal comparability.',
        ],
    }


def save_experiment_metadata(metadata, destination):
    """Exclusive create. Refuse overwrite; serialize fully before opening output."""
    payload = json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + '\n'
    path = Path(destination)
    # Parent must exist: no implicit directory mutations.
    with path.open('x', encoding='utf-8') as output:
        output.write(payload)
    return path
