"""Opt-in numeric live-role pilot. No change to Slack or durable review workflow."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from src.request_budget import RequestBudget
from src.review_workflow import digest
from src.role_comparison import (ROOT, run_roles, validate_evidence,
                                 validate_findings, validate_proposal)

FIXTURES = ROOT / 'evaluation/role_comparison_live_v1.json'
PROMPTS = ROOT / 'evaluation/prompts/role_live_v1.json'
CODE = ['role_comparison_live.py', 'role_comparison.py', 'request_budget.py',
        'llm_client.py', 'config.py', 'review_workflow.py', 'structured_output.py',
        'verification_pipeline.py', 'guardrail.py', 'narrative_composer.py']


def assets():
    fixtures = json.loads(FIXTURES.read_text(encoding='utf-8'))
    prompts = json.loads(PROMPTS.read_text(encoding='utf-8'))
    if (fixtures['version'] != 'numeric-live-fixtures-v1'
            or len(fixtures['paired']) != 3 or len(fixtures['challenges']) != 5
            or set(prompts) != {'version', 'analysis', 'reviewer'}):
        raise ValueError('Unsupported live pilot assets')
    ids = set()
    for case in fixtures['paired'] + fixtures['challenges']:
        if not isinstance(case['case_id'], str) or case['case_id'] in ids:
            raise ValueError('Case IDs must be unique strings')
        ids.add(case['case_id'])
        validate_evidence(case['evidence'])
        if 'proposal' in case:
            validate_proposal(case['proposal'])
            if type(case['reference_publishable']) is not bool:
                raise ValueError('Invalid reference label')
    return fixtures, prompts


def make_plan(*, suite='smoke', retain_rejected_output=False):
    """Credential-free; importing configuration does not construct an SDK client."""
    if suite not in ('smoke', 'pilot'):
        raise ValueError('Suite must be smoke or pilot')
    if type(retain_rejected_output) is not bool:
        raise ValueError('Rejected-output option must be boolean')
    from src.config import PROVIDERS
    config = PROVIDERS['gemini']
    if config['adapter'] != 'openai_compatible':
        raise ValueError('This pilot requires the configured Gemini OpenAI-compatible adapter')
    fixtures, prompts = assets()
    return dict(experiment='numeric-live-role-pilot-v1', suite=suite, report_version=2,
                retain_rejected_output=retain_rejected_output, rejected_output_max_bytes=4096,
                provider='gemini', adapter=config['adapter'], configured_model=config['model'],
                fixture_hash=digest(fixtures), prompt_hash=digest(prompts),
                code_hash=digest({name: (ROOT / 'src' / name).read_text(encoding='utf-8') for name in CODE}),
                maximum_provider_requests=2 if suite == 'smoke' else 11,
                paired_cases=1 if suite == 'smoke' else 3,
                reviewer_challenges=0 if suite == 'smoke' else 5,
                sdk_max_retries=0, sdk_timeout_seconds=60.0, max_output_tokens=2048,
                minimum_request_interval_seconds=5.0,
                generation_settings='No temperature or reasoning overrides; configured provider defaults.',
                scope='numeric score/bin only; no policy retrieval, repair, tools or analyst actions')


class RoleOutputError(ValueError):
    """Static diagnostic codes only; never carry provider/model exception text."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _check_response(raw):
    choices = getattr(raw, 'choices', None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise RoleOutputError('response_shape_invalid')
    choice = choices[0]
    message = getattr(choice, 'message', None)
    if message is None:
        raise RoleOutputError('response_shape_invalid')
    if getattr(message, 'tool_calls', None):
        raise RoleOutputError('unexpected_tool_calls')
    finish = getattr(choice, 'finish_reason', None)
    if finish == 'length':
        raise RoleOutputError('output_token_limit')
    if finish != 'stop':
        raise RoleOutputError('response_not_completed')
    text = getattr(message, 'content', None)
    if type(text) is not str:
        raise RoleOutputError('response_text_invalid')
    if not text.strip():
        raise RoleOutputError('response_text_empty')
    if len(text.encode('utf-8')) > 16384:
        raise RoleOutputError('response_size_limit')


def _strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RoleOutputError('json_duplicate_key')
            result[key] = value
        return result
    def invalid_constant(_):
        raise RoleOutputError('json_nonfinite_number')
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except json.JSONDecodeError:
        raise RoleOutputError('json_invalid') from None


def _safe_usage(raw):
    usage = getattr(raw, 'usage', None)
    fields = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    values = {key: getattr(usage, key, None) for key in fields}
    return values if all(type(v) is int and v >= 0 for v in values.values()) else None


class LiveRoles:
    """Only adapter.chat is exposed; tools, continuations and retries are absent."""
    def __init__(self, adapter, budget, prompts, *, clock=perf_counter, rejected_output_directory=None):
        self.adapter, self.budget, self.prompts, self.clock = adapter, budget, prompts, clock
        self.calls = []
        self.rejected_output_directory = rejected_output_directory
        self.raw_response = None
        self.configured_sdk = budget.wrap_adapter(adapter, 'openai_compatible')
        counted_create = adapter.client.chat.completions.create
        def create(**kwargs):
            # Bound below adapter.chat so every actual SDK attempt is counted.
            kwargs['max_tokens'] = 2048
            kwargs.pop('tools', None)
            self.raw_response = counted_create(**kwargs)
            _check_response(self.raw_response)
            return self.raw_response
        adapter.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def close(self):
        self.configured_sdk.close()

    def call(self, role, evidence, proposal=None):
        payload = {'evidence': deepcopy(evidence)}
        if role == 'reviewer':
            payload['proposal'] = deepcopy(proposal)
        record = dict(case_id=getattr(self, 'case_id', None), role=role, request_attempted=False, status='failed',
                      error_category=None, failure_detail=None, usage=None, response_model=None)
        before, started = self.budget.started, self.clock()
        self.raw_response = None
        try:
            response = self.adapter.chat([
                {'role': 'system', 'content': self.prompts[role]},
                {'role': 'user', 'content': json.dumps(payload, allow_nan=False)}])
            parsed = _strict_json(response.text)
            try:
                if role == 'analysis':
                    parsed = validate_proposal(parsed)
                else:
                    validate_findings(parsed, evidence)
            except (ValueError, TypeError, KeyError):
                raise RoleOutputError(role + '_contract_invalid') from None
            record['status'] = 'valid'
            return parsed
        except Exception as exc:
            # Never persist raw exception strings, SDK objects or credentials.
            if isinstance(exc, RoleOutputError):
                record['failure_detail'] = exc.code
            elif self.raw_response is not None:
                record['failure_detail'] = 'response_normalization_or_processing_failed'
            elif self.budget.started == before:
                record['failure_detail'] = 'request_not_started'
            else:
                record['failure_detail'] = 'provider_request_failed'
            if self.raw_response is not None:
                record['error_category'] = 'invalid_model_output'
            elif self.budget.started == before:
                record['error_category'] = 'request_not_started'
            else:
                record['error_category'] = 'provider_request_failed'
            self._capture_rejected(record)
            raise RoleOutputError('role_failed') from None
        finally:
            record['request_attempted'] = self.budget.started > before
            record['request_number'] = self.budget.started if record['request_attempted'] else None
            record['elapsed_ms'] = max(0.0, (self.clock() - started) * 1000)
            # Includes pacing, adapter normalization and parsing; not pure network latency.
            if self.raw_response is not None:
                record['usage'] = _safe_usage(self.raw_response)
                model = getattr(self.raw_response, 'model', None)
                record['response_model'] = model if isinstance(model, str) and len(model) <= 160 else None
            self.calls.append(record)

    def _capture_rejected(self, record):
        """Opt-in local text only. No exception, request, credentials or SDK dump."""
        if self.rejected_output_directory is None or self.raw_response is None:
            return
        choices = getattr(self.raw_response, 'choices', None)
        if not isinstance(choices, list) or not choices:
            return
        text = getattr(getattr(choices[0], 'message', None), 'content', None)
        if type(text) is not str:
            return
        encoded = text.encode('utf-8')
        value = dict(unverified=True, truncated=len(encoded) > 4096,
                     retained_text=encoded[:4096].decode('utf-8', errors='ignore'))
        filename = f"rejected-{self.budget.started:03d}-{record['role']}.json"
        try:
            _write(Path(self.rejected_output_directory) / filename, value)
            record['rejected_output_file'] = filename
        except OSError:
            record['rejected_output_capture_failed'] = True


def reference_assessment(evidence, proposal):
    """Structured evidence assessment, not a semantic review of draft prose."""
    if proposal is None:
        return 'invalid_or_unavailable'
    request = evidence['request']
    available = any(r['period'] == request['period'] and r['market'] == request['market']
                    for r in evidence['observations'])
    abstains = not proposal['score_claims'] and not proposal['policy_claims'] and bool(proposal['limitations'])
    if abstains:
        return 'unjustified_abstention' if available else 'justified_abstention'
    return 'publishable_numeric' if reference_correct(evidence, proposal) else 'incorrect_or_incomplete_numeric'


def reference_correct(evidence, proposal):
    """Independent exact-request oracle, not a claim that the gate is infallible."""
    if proposal is None:
        return False
    request = evidence['request']
    rows = [r for r in evidence['observations']
            if r['period'] == request['period'] and r['market'] == request['market']]
    if len(rows) != 1:
        return False
    expected = [dict(**request, metric=m, value=rows[0][m]) for m in ('score', 'bin')]
    return (not proposal['policy_claims'] and not proposal['limitations']
            and len(proposal['score_claims']) == 2
            and all(sum(c == e for c in proposal['score_claims']) == 1 for e in expected))


def _pair(evidence, proposal, reviewer):
    def replay(_):
        if proposal is None:
            raise RoleOutputError('No valid analysis proposal')
        return deepcopy(proposal)
    baseline = run_roles(evidence, replay)
    reviewed = run_roles(evidence, replay, reviewer)
    if (baseline['snapshot_hash'] != reviewed['snapshot_hash']
            or baseline['proposal_hash'] != reviewed['proposal_hash']):
        raise ValueError('Paired evidence/proposal hash mismatch')
    assessment = reference_assessment(evidence, proposal)
    reference = assessment == 'publishable_numeric'
    for result in (baseline, reviewed):
        result['reference_assessment'] = assessment
        result['reference_numeric_package_publishable'] = reference
        result['justified_abstention'] = assessment == 'justified_abstention'
        result['reference_proposal_correct'] = reference  # Legacy field: numeric publishability only.
        result['incorrect_publication'] = result['publishable'] and not reference
        # Failure cases are availability failures; distinguish from false alarms.
        result['held_correct_proposal'] = not result['publishable'] and reference
    return dict(analysis_only=baseline, analysis_plus_review=reviewed)


def _write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def run_live(plan, destination, *, daily_remaining, confirm_live_api=False,
             client_factory=None, budget_factory=RequestBudget):
    if confirm_live_api is not True:
        raise ValueError('Explicit live opt-in is required')
    if plan != make_plan(suite=plan['suite'], retain_rejected_output=plan['retain_rejected_output']):
        raise ValueError('Stale live plan; create a fresh plan')
    budget = budget_factory(daily_remaining=daily_remaining,
                            max_requests=plan['maximum_provider_requests'])
    if budget.started or budget.limit < plan['maximum_provider_requests']:
        raise ValueError('Declare enough remaining requests for the selected suite')
    fixtures, prompts = assets()
    destination = Path(destination)
    destination.mkdir()  # reserve a NEW bundle before credentials/client access
    _write(destination / 'plan.json', plan)
    report = dict(plan=plan, started_at=datetime.now(timezone.utc).isoformat(),
                  status='incomplete', execution_mode='injected_sdk' if client_factory else 'configured_provider',
                  paired=[], challenges=[], calls=[], estimated_live_cost=None,
                  cost_unavailable_reason='No price assumptions; this pilot does not calculate monetary cost.',
                  token_usage=None, errors=[])
    roles, adapter = None, None
    budget.attach_log(destination / 'requests.jsonl')
    try:
        from src.llm_client import get_llm_client, OpenAICompatibleClient
        adapter = (client_factory or get_llm_client)('gemini')
        if type(adapter) is not OpenAICompatibleClient or adapter.model != plan['configured_model']:
            raise ValueError('Constructed adapter/model differs from the plan')
        if plan != make_plan(suite=plan['suite'], retain_rejected_output=plan['retain_rejected_output']):
            raise ValueError('Source changed during setup')
        roles = LiveRoles(adapter, budget, prompts,
                          rejected_output_directory=destination if plan['retain_rejected_output'] else None)
        for case in fixtures['paired'][:plan['paired_cases']]:
            evidence = case['evidence']
            roles.case_id = case['case_id']
            try:
                proposal = roles.call('analysis', evidence)
            except RoleOutputError:
                proposal = None
            # Local artifact contains UNVERIFIED drafts, never public text.
            _write(destination / (case['case_id'] + '.proposal.json'),
                   dict(unverified=True, proposal=proposal))
            analysis_ms = roles.calls[-1]['elapsed_ms']
            pair = _pair(evidence, proposal, lambda e, p: roles.call('reviewer', e, p))
            for result in pair.values():
                result['analysis_plus_path_elapsed_ms'] = analysis_ms + result['elapsed_ms']
            report['paired'].append(dict(case_id=case['case_id'], **pair))
            _write(destination / (case['case_id'] + '.result.json'), report['paired'][-1])
        for case in fixtures['challenges'][:plan['reviewer_challenges']]:
            roles.case_id = case['case_id']
            pair = _pair(case['evidence'], case['proposal'], lambda e, p: roles.call('reviewer', e, p))
            if pair['analysis_only']['reference_proposal_correct'] != case['reference_publishable']:
                raise ValueError('Challenge reference mismatch')
            findings = pair['analysis_plus_review']['reviewer_findings']
            expected = {tuple(x) for x in case['expected_findings']}
            actual = {(x['code'], x['metric']) for x in findings}
            report['challenges'].append(dict(case_id=case['case_id'], **pair,
                expected_findings=sorted(expected), matched_findings=sorted(actual & expected),
                missed_findings=sorted(expected - actual), unexpected_findings=sorted(actual - expected)))
            _write(destination / (case['case_id'] + '.result.json'), report['challenges'][-1])
        report['status'] = 'completed_with_role_failures' if any(c['status'] != 'valid' for c in roles.calls) else 'completed'
    except Exception:
        report['errors'].append('pilot_setup_or_execution_failed')
    finally:
        if roles:
            report['calls'] = roles.calls
            try:
                roles.close()
            except Exception:
                report['errors'].append('client_cleanup_failed')
        report['budget'] = budget.summary()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        try:
            report['source_unchanged'] = plan == make_plan(suite=plan['suite'], retain_rejected_output=plan['retain_rejected_output'])
        except Exception:
            report['source_unchanged'] = False
        if not report['source_unchanged']:
            report['status'] = 'incomplete'
        report['latency_scope'] = ('Role call elapsed includes pacing, SDK, normalization and parsing; '
            'paired path totals reuse analysis elapsed and add graph.invoke elapsed, excluding compilation. '
            'These are sequential pilot timings, not independent production latency samples.')
        report['summary'] = {}
        for group in ('paired', 'challenges'):
            report['summary'][group] = {}
            for mode in ('analysis_only', 'analysis_plus_review'):
                results = [row[mode] for row in report[group]]
                report['summary'][group][mode] = dict(cases=len(results),
                    publications=sum(r['publishable'] for r in results),
                    incorrect_publications=sum(r['incorrect_publication'] for r in results),
                    held_correct_proposals=sum(r['held_correct_proposal'] for r in results),
                    justified_abstentions=sum(r['justified_abstention'] for r in results),
                    unjustified_abstentions=sum(r['reference_assessment'] == 'unjustified_abstention' for r in results),
                    incorrect_or_incomplete_numeric_proposals=sum(r['reference_assessment'] == 'incorrect_or_incomplete_numeric' for r in results),
                    invalid_or_unavailable_proposals=sum(r['reference_assessment'] == 'invalid_or_unavailable' for r in results))
        report['usage_unavailable_reason'] = 'Per-call provider usage only; missing values are not estimated.'
        usages = [c['usage'] for c in report['calls'] if c['request_attempted']]
        if usages and all(u is not None for u in usages):
            report['token_usage'] = {k: sum(u[k] for u in usages) for k in usages[0]}
        budget.close()
        _write(destination / 'report.json', report)
    return report
