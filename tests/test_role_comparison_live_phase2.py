"""Fake SDK tests: no API keys, provider requests, or sleeps."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from src.llm_client import OpenAICompatibleClient
from src.request_budget import RequestBudget, RequestBudgetExceeded
from src.role_comparison_live import (LiveRoles, RoleOutputError, assets, make_plan,
                                      reference_correct, run_live)


def proposal(evidence):
    request = evidence['request']
    row = next((r for r in evidence['observations'] if r['period'] == request['period']), None)
    return dict(answer='PRIVATE_UNVERIFIED_DRAFT',
                score_claims=[] if row is None else [dict(**request, metric=m, value=row[m]) for m in ('score', 'bin')],
                policy_claims=[], limitations=[] if row else ['No requested month.'])


def response(text, *, usage=True, finish='stop', tools=None):
    return NS(choices=[NS(finish_reason=finish, message=NS(content=text, tool_calls=tools))],
              model='fake-served-model', usage=NS(prompt_tokens=10, completion_tokens=20, total_tokens=30) if usage else None)


class FakeSDK:
    def __init__(self, handler=None):
        self.handler = handler
        self.requests, self.options, self.closed = [], [], False
        self.max_retries = 2
        self.chat = NS(completions=NS(create=self.create))
    def with_options(self, **kwargs):
        self.options.append(kwargs)
        self.max_retries = kwargs['max_retries']
        return self
    def close(self):
        self.closed = True
    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        if self.handler:
            return self.handler(kwargs)
        evidence = json.loads(kwargs['messages'][1]['content'])['evidence']
        payload = json.loads(kwargs['messages'][1]['content'])
        return response(json.dumps({'findings': []} if 'proposal' in payload else proposal(evidence)))


def fast_budget(**kwargs):
    t = [0.0]
    def sleep(seconds):
        t[0] += seconds
    return RequestBudget(**kwargs, sleeper=sleep, clock=lambda: t[0])


def execute(tmp_path, sdk, suite='smoke'):
    plan = make_plan(suite=suite)
    def factory(provider):
        assert provider == 'gemini'
        return OpenAICompatibleClient(sdk, plan['configured_model'])
    return run_live(plan, tmp_path/'run', daily_remaining=plan['maximum_provider_requests'],
                    confirm_live_api=True, client_factory=factory, budget_factory=fast_budget)


def test_dry_plan_never_constructs_client_or_needs_key(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with patch('src.llm_client.get_llm_client', side_effect=AssertionError('network')):
        assert make_plan()['maximum_provider_requests'] == 2
        assert make_plan(suite='pilot')['maximum_provider_requests'] == 11


def test_smoke_pairs_one_proposal_and_accounts_actual_requests(tmp_path):
    sdk = FakeSDK()
    report = execute(tmp_path, sdk)
    assert report['status'] == 'completed'
    assert len(sdk.requests) == report['budget']['requests_started'] == 2
    assert sdk.options == [{'max_retries': 0, 'timeout': 60.0}]
    assert sdk.closed
    assert all(r['max_tokens'] == 2048 and 'tools' not in r for r in sdk.requests)
    a, b = (report['paired'][0][key] for key in ('analysis_only', 'analysis_plus_review'))
    assert a['proposal_hash'] == b['proposal_hash']
    assert a['snapshot_hash'] == b['snapshot_hash']
    assert a['publishable'] and b['publishable']
    assert a['role_invocations'] == {'analysis': 1, 'reviewer': 0}
    assert b['role_invocations'] == {'analysis': 1, 'reviewer': 1}
    assert report['token_usage'] == dict(prompt_tokens=20, completion_tokens=40, total_tokens=60)
    assert report['estimated_live_cost'] is None
    assert 'PRIVATE_UNVERIFIED_DRAFT' not in json.dumps(report)
    assert 'PRIVATE_UNVERIFIED_DRAFT' in (tmp_path/'run/LP-A1.proposal.json').read_text()
    for request in sdk.requests:
        payload = json.loads(request['messages'][1]['content'])
        assert set(payload) in ({'evidence'}, {'evidence', 'proposal'})
        assert 'reference_publishable' not in json.dumps(payload)
        assert 'expected_findings' not in json.dumps(payload)


def test_full_pilot_11_requests_and_controlled_challenges_separate(tmp_path):
    sdk = FakeSDK()
    report = execute(tmp_path, sdk, 'pilot')
    assert report['budget']['requests_started'] == len(sdk.requests) == 11
    assert len(report['paired']) == 3 and len(report['challenges']) == 5
    assert report['summary']['paired']['analysis_only']['incorrect_publications'] == 0
    assert report['summary']['challenges']['analysis_plus_review']['incorrect_publications'] == 0
    assert all(not row['analysis_plus_review']['publishable'] for row in report['challenges'][1:])
    assert any(row['missed_findings'] for row in report['challenges'][1:])
    log = [json.loads(line) for line in (tmp_path/'run/requests.jsonl').read_text().splitlines()]
    assert sum(e['event'] == 'request_start' for e in log) == 11


@pytest.mark.parametrize('bad', ['not JSON', '{}', '{"findings":[],"override":true}',
                                '{"findings":[],"findings":[]}', '{"findings":NaN}'])
def test_invalid_reviewer_output_holds_without_changing_gate(tmp_path, bad):
    sdk = FakeSDK()
    def handler(kw):
        payload = json.loads(kw['messages'][1]['content'])
        return response(bad if 'proposal' in payload else json.dumps(proposal(payload['evidence'])))
    sdk.handler = handler
    report = execute(tmp_path, sdk)
    reviewed = report['paired'][0]['analysis_plus_review']
    assert not reviewed['publishable']
    assert reviewed['verification_status'] == 'passed'
    assert reviewed['reason_codes'] == ['REVIEWER_INVALID_OR_FAILED']
    assert report['calls'][1]['error_category'] == 'invalid_model_output'
    assert report['status'] == 'completed_with_role_failures'


def test_false_alarm_holds_valid_proposal(tmp_path):
    def handler(kw):
        payload = json.loads(kw['messages'][1]['content'])
        value = {'findings': [{'code': 'unsupported_claim', 'metric': 'score', 'evidence_ids': ['obs-1991-12']}]} if 'proposal' in payload else proposal(payload['evidence'])
        return response(json.dumps(value))
    report = execute(tmp_path, FakeSDK(handler))
    reviewed = report['paired'][0]['analysis_plus_review']
    assert reviewed['held_correct_proposal']
    assert reviewed['reason_codes'] == ['REVIEWER_REQUESTED_INSPECTION']


@pytest.mark.parametrize('failure', ['malformed', 'timeout', 'tool', 'truncated'])
def test_analysis_failure_skips_reviewer_and_never_retries(tmp_path, failure):
    def handler(_):
        if failure == 'timeout':
            raise TimeoutError('SECRET_EXCEPTION_SENTINEL')
        if failure == 'tool':
            return response('{}', tools=[NS(id='x', function=NS(name='forbidden', arguments='{}'))])
        return response('{}', finish='length' if failure == 'truncated' else 'stop')
    sdk = FakeSDK(handler)
    report = execute(tmp_path, sdk)
    assert len(sdk.requests) == 1
    assert report['budget']['requests_started'] == 1
    assert report['paired'][0]['analysis_plus_review']['role_invocations']['reviewer'] == 0
    assert not report['paired'][0]['analysis_only']['publishable']
    assert 'SECRET_EXCEPTION_SENTINEL' not in json.dumps(report)


def test_provider_reviewer_failure_safe_and_usage_unknown(tmp_path):
    def handler(kw):
        payload = json.loads(kw['messages'][1]['content'])
        if 'proposal' in payload:
            raise TimeoutError('DO_NOT_LOG')
        return response(json.dumps(proposal(payload['evidence'])), usage=False)
    report = execute(tmp_path, FakeSDK(handler))
    assert report['budget']['requests_started'] == 2
    assert report['token_usage'] is None
    assert report['calls'][1]['error_category'] == 'provider_request_failed'
    assert 'DO_NOT_LOG' not in json.dumps(report)


def test_request_cap_counts_failures_below_adapter():
    sdk = FakeSDK(lambda _: (_ for _ in ()).throw(TimeoutError('private')))
    budget = fast_budget(daily_remaining=1, max_requests=1)
    adapter = OpenAICompatibleClient(sdk, 'fake')
    roles = LiveRoles(adapter, budget, assets()[1])
    evidence = assets()[0]['paired'][0]['evidence']
    for _ in range(2):
        with pytest.raises(RoleOutputError):
            roles.call('analysis', evidence)
    assert budget.started == len(sdk.requests) == 1
    assert roles.calls[1]['error_category'] == 'request_not_started'


@pytest.mark.parametrize('which', ['opt_in', 'allowance', 'stale', 'existing'])
def test_preflight_rejects_before_factory_or_spending(tmp_path, which):
    plan = make_plan()
    destination = tmp_path/'run'
    if which == 'stale':
        plan['configured_model'] = 'different'
    if which == 'existing':
        destination.mkdir()
    with pytest.raises((ValueError, FileExistsError)):
        run_live(plan, destination, daily_remaining=1 if which == 'allowance' else 2,
                 confirm_live_api=which != 'opt_in',
                 client_factory=lambda _: pytest.fail('No factory access expected'), budget_factory=fast_budget)


def test_setup_failure_retains_incomplete_sanitized_bundle(tmp_path):
    def fail(_):
        raise RuntimeError('PRIVATE_KEY_SENTINEL')
    report = run_live(make_plan(), tmp_path/'run', daily_remaining=2, confirm_live_api=True,
                      client_factory=fail, budget_factory=fast_budget)
    assert report['status'] == 'incomplete'
    assert report['budget']['requests_started'] == 0
    assert (tmp_path/'run/report.json').exists()
    assert 'PRIVATE_KEY_SENTINEL' not in json.dumps(report)


def test_reference_distinguishes_wrong_context_duplicates_and_limitations():
    evidence = assets()[0]['paired'][0]['evidence']
    good = proposal(evidence)
    assert reference_correct(evidence, good)
    for mutate in [lambda p: p['score_claims'].append(p['score_claims'][0]),
                   lambda p: p['score_claims'][0].update(market=2),
                   lambda p: p['limitations'].append('Needs inspection')]:
        changed = deepcopy(good)
        mutate(changed)
        assert not reference_correct(evidence, changed)


@pytest.mark.parametrize('http_status', [200, 500])
def test_real_pinned_sdk_mock_transport_obeys_bounds_without_network(tmp_path, http_status):
    import httpx
    from openai import OpenAI
    attempts = []
    def transport(request):
        body = json.loads(request.content)
        attempts.append(body)
        assert body['max_tokens'] == 2048
        assert 'tools' not in body
        if http_status == 500:
            return httpx.Response(500, json={'error': {'message': 'PRIVATE_HTTP_ERROR'}})
        payload = json.loads(body['messages'][1]['content'])
        result = {'findings': []} if 'proposal' in payload else proposal(payload['evidence'])
        return httpx.Response(200, json={
            'id': 'fake-response', 'object': 'chat.completion', 'created': 0,
            'model': 'fake-served-model',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': json.dumps(result)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}})
    def factory(_):
        sdk = OpenAI(api_key='offline-placeholder', base_url='https://offline.invalid/v1/',
                     http_client=httpx.Client(transport=httpx.MockTransport(transport)))
        return OpenAICompatibleClient(sdk, make_plan()['configured_model'])
    report = run_live(make_plan(), tmp_path/'run', daily_remaining=2, confirm_live_api=True,
                      client_factory=factory, budget_factory=fast_budget)
    expected = 2 if http_status == 200 else 1
    assert len(attempts) == report['budget']['requests_started'] == expected
    assert report['status'] == ('completed' if http_status == 200 else 'completed_with_role_failures')
    assert 'PRIVATE_HTTP_ERROR' not in json.dumps(report)


@pytest.mark.parametrize('case_index,expected', [(0, 'publishable_numeric'), (1, 'justified_abstention'), (2, 'publishable_numeric')])
def test_reference_assessment_separates_correct_abstention(case_index, expected):
    from src.role_comparison_live import reference_assessment
    evidence = assets()[0]['paired'][case_index]['evidence']
    assert reference_assessment(evidence, proposal(evidence)) == expected
    # Abstention has no numeric publication; the legacy boolean remains false.
    if expected == 'justified_abstention':
        assert not reference_correct(evidence, proposal(evidence))


def test_reference_assessment_rejects_fabrication_and_unjustified_abstention():
    from src.role_comparison_live import reference_assessment
    evidence = assets()[0]['paired'][0]['evidence']
    abstention = dict(answer='Not enough data.', score_claims=[], policy_claims=[], limitations=['Missing data.'])
    assert reference_assessment(evidence, abstention) == 'unjustified_abstention'
    assert reference_assessment(evidence, None) == 'invalid_or_unavailable'
    bad = proposal(evidence)
    bad['score_claims'][0]['value'] += 1
    assert reference_assessment(evidence, bad) == 'incorrect_or_incomplete_numeric'
    absent = assets()[0]['paired'][1]['evidence']
    assert reference_assessment(absent, bad) == 'incorrect_or_incomplete_numeric'
    abstention['limitations'] = []
    assert reference_assessment(absent, abstention) == 'incorrect_or_incomplete_numeric'


def test_report_counts_abstention_without_changing_gate(tmp_path):
    report = execute(tmp_path, FakeSDK(), 'pilot')
    for mode in ('analysis_only', 'analysis_plus_review'):
        row = report['paired'][1][mode]
        assert row['reference_assessment'] == 'justified_abstention'
        assert row['justified_abstention'] and not row['reference_numeric_package_publishable']
        assert not row['publishable'] and not row['incorrect_publication']
        assert report['summary']['paired'][mode]['justified_abstentions'] == 1
        assert report['summary']['paired'][mode]['incorrect_or_incomplete_numeric_proposals'] == 0
    assert report['plan']['report_version'] == 2


@pytest.mark.parametrize('raw,expected', [
    (response('broken JSON'), 'json_invalid'),
    (response('{"findings":[],"findings":[]}'), 'json_duplicate_key'),
    (response('{"findings":NaN}'), 'json_nonfinite_number'),
    (response('{}'), 'reviewer_contract_invalid'),
    (response('{"findings":[]}', finish='length'), 'output_token_limit'),
    (response('{"findings":[]}', finish='content_filter'), 'response_not_completed'),
    (response(''), 'response_text_empty'),
    (response(None), 'response_text_invalid'),
    (response('x'*16385), 'response_size_limit'),
    (response('{}', tools=[NS(id='x', function=NS(name='bad', arguments='INVALID_JSON'))]), 'unexpected_tool_calls'),
    (NS(choices=[], usage=None), 'response_shape_invalid'),
])
def test_specific_failure_detail_is_static_and_does_not_retry(raw, expected):
    budget = fast_budget(daily_remaining=2, max_requests=2)
    sdk = FakeSDK(lambda _: raw)
    roles = LiveRoles(OpenAICompatibleClient(sdk, 'fake'), budget, assets()[1])
    evidence = assets()[0]['paired'][0]['evidence']
    with pytest.raises(RoleOutputError):
        roles.call('reviewer', evidence, proposal(evidence))
    assert roles.calls[0]['failure_detail'] == expected
    assert roles.calls[0]['error_category'] == 'invalid_model_output'
    assert budget.started == len(sdk.requests) == 1
    assert 'retained_text' not in json.dumps(roles.calls)


def test_analysis_contract_failure_has_own_code():
    sdk = FakeSDK(lambda _: response('{"findings":[]}'))
    roles = LiveRoles(OpenAICompatibleClient(sdk, 'fake'), fast_budget(daily_remaining=1, max_requests=1), assets()[1])
    with pytest.raises(RoleOutputError):
        roles.call('analysis', assets()[0]['paired'][0]['evidence'])
    assert roles.calls[0]['failure_detail'] == 'analysis_contract_invalid'


@pytest.mark.parametrize('retain', [False, True])
def test_rejected_capture_explicit_bounded_and_not_in_report(tmp_path, retain):
    text = 'REJECTED_TEXT_SENTINEL' + 'é'*9000
    def handler(kw):
        payload = json.loads(kw['messages'][1]['content'])
        return response(text if 'proposal' in payload else json.dumps(proposal(payload['evidence'])))
    sdk = FakeSDK(handler)
    plan = make_plan(retain_rejected_output=retain)
    report = run_live(plan, tmp_path/'run', daily_remaining=2, confirm_live_api=True,
                      client_factory=lambda _: OpenAICompatibleClient(sdk, plan['configured_model']),
                      budget_factory=fast_budget)
    files = list((tmp_path/'run').glob('rejected-*.json'))
    assert len(files) == int(retain)
    assert report['calls'][1]['failure_detail'] == 'response_size_limit'
    assert 'REJECTED_TEXT_SENTINEL' not in json.dumps(report)
    assert report['status'] == 'completed_with_role_failures'
    if retain:
        data = json.loads(files[0].read_text())
        assert data['unverified'] and data['truncated']
        assert len(data['retained_text'].encode('utf-8')) <= 4096
        assert report['calls'][1]['rejected_output_file'] == files[0].name


def test_capture_never_writes_provider_exception_text(tmp_path):
    def fail(_):
        raise RuntimeError('PRIVATE_PROVIDER_EXCEPTION')
    roles = LiveRoles(OpenAICompatibleClient(FakeSDK(fail), 'fake'),
                      fast_budget(daily_remaining=1, max_requests=1), assets()[1],
                      rejected_output_directory=tmp_path)
    with pytest.raises(RoleOutputError):
        roles.call('analysis', assets()[0]['paired'][0]['evidence'])
    assert roles.calls[0]['failure_detail'] == 'provider_request_failed'
    assert not list(tmp_path.iterdir())
    assert 'PRIVATE_PROVIDER_EXCEPTION' not in json.dumps(roles.calls)


def test_capture_write_failure_cannot_publish_or_erase_role_diagnostic(tmp_path):
    roles = LiveRoles(OpenAICompatibleClient(FakeSDK(lambda _: response('bad JSON')), 'fake'),
                      fast_budget(daily_remaining=1, max_requests=1), assets()[1],
                      rejected_output_directory=tmp_path/'nonexistent')
    with pytest.raises(RoleOutputError):
        roles.call('reviewer', assets()[0]['paired'][0]['evidence'], {})
    assert roles.calls[0]['failure_detail'] == 'json_invalid'
    assert roles.calls[0]['rejected_output_capture_failed']


def test_capture_setting_bound_to_plan_before_client_access(tmp_path):
    plan = make_plan()
    plan['retain_rejected_output'] = True
    # Toggling the setting is a legitimate fresh configuration, but the full plan
    # still binds the cap and version. A modified cap must fail preflight.
    plan['rejected_output_max_bytes'] = 1000000
    with pytest.raises(ValueError):
        run_live(plan, tmp_path/'run', daily_remaining=2, confirm_live_api=True,
                 client_factory=lambda _: pytest.fail('No client allowed'), budget_factory=fast_budget)


def test_offline_saved_case_readback_preserves_bundle_and_checks_hashes(tmp_path):
    from scripts.inspect_live_proposal_phase2 import inspect_saved_case
    execute(tmp_path, FakeSDK(), 'pilot')
    directory = tmp_path/'run'
    # Simulate original v1 report: readback must accept its old plan and fields.
    report_file = directory/'report.json'
    report = json.loads(report_file.read_text())
    report['plan'].pop('report_version')
    for case in report['paired']:
        for mode in ('analysis_only', 'analysis_plus_review'):
            case[mode].pop('reference_assessment')
    report_file.write_text(json.dumps(report))
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with patch('src.llm_client.get_llm_client', side_effect=AssertionError('No network')):
        result = inspect_saved_case(directory, 'LP-A2')
    assert result['current_reference_assessment'] == 'justified_abstention'
    assert result['original_reference_proposal_correct'] is False
    assert result['provider_requests'] == 0
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    saved = json.loads((directory/'LP-A2.proposal.json').read_text())
    saved['proposal']['limitations'].append('Changed')
    (directory/'LP-A2.proposal.json').write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        inspect_saved_case(directory, 'LP-A2')
