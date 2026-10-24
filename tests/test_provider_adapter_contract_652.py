"""Offline transport contracts for actual adapters; no SDKs, keys, or agent needed.

Fake SDK responses exercise the real llm_client adapters. This does not test
live model quality, SDK serialization, the policy evaluator, or the response gate.
"""
import copy
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace as NS

from src.llm_client import AnthropicClient, OpenAICompatibleClient

# Representative transport schemas; policy semantics remain covered by 6.4.5.
TOOLS = [
    {'name': 'get_history', 'description': 'Read synthetic history.',
     'input_schema': {'type': 'object', 'properties': {'account_id': {'type': 'integer'}},
                      'required': ['account_id']}},
    {'name': 'retrieve_policy', 'description': 'Read synthetic passages.',
     'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'},
                      'k': {'type': 'integer'}}, 'required': ['query']}},
    {'name': 'evaluate_policy_signals', 'description': 'Evaluate bound synthetic history.',
     'input_schema': {'type': 'object', 'properties': {'account_id': {'type': 'integer'},
                      'as_of_period': {'type': 'string'}},
                      'required': ['account_id', 'as_of_period']}},
]
MESSAGES = [{'role': 'system', 'content': 'Use verified synthetic evidence.'},
            {'role': 'user', 'content': 'Describe the requested account.'}]
CALLS = [('history-1', 'get_history', {'account_id': 123}),
         ('policy-1', 'retrieve_policy', {'query': 'POL-002', 'k': 3})]
ASSIST = [('assist-2', 'evaluate_policy_signals',
           {'account_id': 123, 'as_of_period': '2025-04-01'})]
RESULTS = [[{'period': '2025-04-01', 'score': 550}],
           [{'source': 'POL-002_synthetic.md', 'text': 'Synthetic rules.'}]]


def sdk_response(kind, calls=(), text='', stop=None):
    if kind == 'openai':
        message = NS(content=text, tool_calls=[NS(id=i, function=NS(
            name=n, arguments=json.dumps(a))) for i, n, a in calls] or None)
        return NS(choices=[NS(message=message, finish_reason='stop')])
    blocks = ([NS(type='text', text=text)] if text else []) + [
        NS(type='tool_use', id=i, name=n, input=copy.deepcopy(a)) for i, n, a in calls]
    return NS(content=blocks, stop_reason=stop or ('tool_use' if calls else 'end_turn'),
              usage=NS(input_tokens=20, output_tokens=10))


class RecordingSDK:
    """Snapshot mutable request lists at call time, as a transport would."""
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.chat = NS(completions=NS(create=self.create))
        self.messages = NS(create=self.create)

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def build(kind, replies):
    sdk = RecordingSDK(replies)
    cls = OpenAICompatibleClient if kind == 'openai' else AnthropicClient
    return cls(sdk, model='configured-test-model'), sdk


class ProviderAdapterContractTests(unittest.TestCase):
    def setUp(self):
        # Existing Anthropic diagnostic output is not a transport assertion.
        self.capture = redirect_stdout(StringIO())
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def test_equivalent_schema_and_system_content(self):
        for kind in ('openai', 'anthropic'):
            with self.subTest(adapter=kind):
                tools, messages = copy.deepcopy(TOOLS), copy.deepcopy(MESSAGES)
                client, sdk = build(kind, [sdk_response(kind, text='done')])
                client.chat(messages, tools)
                request = sdk.requests[0]
                self.assertEqual(request['model'], 'configured-test-model')
                if kind == 'openai':
                    schemas = [dict(name=t['function']['name'], description=t['function']['description'],
                                    input_schema=t['function']['parameters']) for t in request['tools']]
                    self.assertEqual(request['messages'], MESSAGES)
                else:
                    schemas = request['tools']
                    self.assertEqual(request['system'], MESSAGES[0]['content'])
                    self.assertEqual(request['messages'], MESSAGES[1:])
                self.assertEqual(schemas, TOOLS)
                self.assertEqual(tools, TOOLS)
                self.assertEqual(messages, MESSAGES)

    def test_multiple_calls_normalize_ids_names_and_arguments(self):
        results = []
        for kind in ('openai', 'anthropic'):
            client, _ = build(kind, [sdk_response(kind, CALLS, 'Gathering evidence')])
            results.append(client.chat(MESSAGES, TOOLS))
        self.assertEqual(results[0], results[1])
        self.assertEqual([(c.id, c.name, c.arguments) for c in results[0].tool_calls], CALLS)
        self.assertEqual(results[0].text, 'Gathering evidence')

    def test_two_tool_rounds_preserve_payloads_and_call_ids(self):
        for kind in ('openai', 'anthropic'):
            with self.subTest(adapter=kind):
                replies = [sdk_response(kind, CALLS), sdk_response(kind, ASSIST),
                           sdk_response(kind, text='final structured response')]
                client, sdk = build(kind, replies)
                first = client.chat(MESSAGES, TOOLS)
                second = client.continue_with_tool_results(first, RESULTS, TOOLS)
                payload = {'warning_signal_status': 'insufficient_evidence',
                           'coverage_complete': False, 'missing_baseline_months': ['2024-09-01']}
                final = client.continue_with_tool_results(second, [payload], TOOLS)
                self.assertEqual(final.text, 'final structured response')
                self.assertEqual(final.tool_calls, [])
                self.assertEqual(len(sdk.requests), 3)
                last = sdk.requests[-1]
                if kind == 'openai':
                    blocks = [m for m in last['messages'] if isinstance(m, dict) and m.get('role') == 'tool']
                    ids = [m['tool_call_id'] for m in blocks]
                    assistants = [m for m in last['messages'] if not isinstance(m, dict)]
                    self.assertEqual([[c.id for c in m.tool_calls] for m in assistants],
                                     [['history-1', 'policy-1'], ['assist-2']])
                else:
                    blocks = [b for m in last['messages'] if m['role'] == 'user'
                              and isinstance(m['content'], list) for b in m['content']]
                    ids = [b['tool_use_id'] for b in blocks]
                    assistants = [m for m in last['messages'] if m['role'] == 'assistant']
                    self.assertEqual([[b.id for b in m['content'] if b.type == 'tool_use']
                                      for m in assistants], [['history-1', 'policy-1'], ['assist-2']])
                self.assertEqual(ids, ['history-1', 'policy-1', 'assist-2'])
                self.assertEqual([json.loads(b['content']) for b in blocks], RESULTS + [payload])
                self.assertEqual(last['tools'], sdk.requests[0]['tools'])

    def test_missing_or_excess_results_rejected_before_sdk_call(self):
        for kind in ('openai', 'anthropic'):
            for count in (0, 1, 3):
                with self.subTest(adapter=kind, count=count):
                    client, sdk = build(kind, [sdk_response(kind, CALLS)])
                    first = client.chat(MESSAGES, TOOLS)
                    with self.assertRaises(ValueError):
                        client.continue_with_tool_results(first, [{}] * count, TOOLS)
                    self.assertEqual(len(sdk.requests), 1)

    def test_new_chat_resets_previous_case(self):
        for kind in ('openai', 'anthropic'):
            with self.subTest(adapter=kind):
                client, sdk = build(kind, [sdk_response(kind, CALLS), sdk_response(kind, text='new')])
                client.chat(MESSAGES, TOOLS)
                new = [{'role': 'system', 'content': 'Fresh context'},
                       {'role': 'user', 'content': 'Different case'}]
                client.chat(new, TOOLS)
                request = sdk.requests[-1]
                self.assertEqual(request['messages'], new if kind == 'openai' else new[1:])
                if kind == 'anthropic': self.assertEqual(request['system'], 'Fresh context')
                self.assertNotIn('history-1', repr(request))

    def test_provider_exception_propagates(self):
        class Simulated429(Exception):
            status_code = 429
        for kind in ('openai', 'anthropic'):
            with self.subTest(adapter=kind):
                failure = Simulated429('synthetic transport failure')
                client, _ = build(kind, [failure])
                with self.assertRaises(Simulated429) as caught:
                    client.chat(MESSAGES, TOOLS)
                self.assertIs(caught.exception, failure)

    def test_openai_invalid_json_arguments_rejected(self):
        response = sdk_response('openai', CALLS)
        response.choices[0].message.tool_calls[0].function.arguments = '{'
        client, _ = build('openai', [response])
        with self.assertRaises(json.JSONDecodeError): client.chat(MESSAGES, TOOLS)

    def test_anthropic_token_truncation_rejected(self):
        client, _ = build('anthropic', [sdk_response('anthropic', text='partial', stop='max_tokens')])
        with self.assertRaisesRegex(RuntimeError, 'max_tokens'):
            client.chat(MESSAGES, TOOLS)

    def test_anthropic_empty_response_rejected(self):
        client, _ = build('anthropic', [sdk_response('anthropic')])
        with self.assertRaisesRegex(RuntimeError, 'neither visible text nor tool calls'):
            client.chat(MESSAGES, TOOLS)

    def test_anthropic_continuation_requires_previous_response(self):
        from src.llm_client import LLMResponse
        client, sdk = build('anthropic', [])
        with self.assertRaisesRegex(RuntimeError, 'No previous assistant response'):
            client.continue_with_tool_results(LLMResponse(text=''), [], TOOLS)
        self.assertEqual(sdk.requests, [])
