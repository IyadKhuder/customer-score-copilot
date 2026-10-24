import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)



def to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    """Convert project tool schemas to OpenAI-compatible function tools."""

    if tools is None:
        return None

    converted = []

    for tool in tools:
        # Already in OpenAI-compatible format? Leave it unchanged.
        if tool.get("type") == "function":
            converted.append(tool)
            continue

        converted.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        })

    return converted



class OpenAICompatibleClient:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model
        self._messages = []

    def chat(self, messages, tools=None) -> LLMResponse:
        self._messages = list(messages)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._messages,
            tools=to_openai_tools(tools),
        )

        assistant_message = response.choices[0].message
        self._messages.append(assistant_message)

        return normalize_openai_response(response)

    def continue_with_tool_results(
            self,
            assistant_response: LLMResponse,
            tool_results: list,
            tools=None,
    ) -> LLMResponse:
        """Continue the conversation after executing requested tools."""

        if len(tool_results) != len(assistant_response.tool_calls):
            raise ValueError(
                "Each tool call must have exactly one tool result."
            )

        for tool_call, tool_result in zip(
                assistant_response.tool_calls,
                tool_results,
        ):
            tool_message = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "content": json.dumps(tool_result),
            }

            self._messages.append(tool_message)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._messages,
            tools=to_openai_tools(tools),
        )

        assistant_message = response.choices[0].message
        self._messages.append(assistant_message)

        return normalize_openai_response(response)


def normalize_openai_response(response) -> LLMResponse:
    """Convert an OpenAI-compatible SDK response into our own response format."""

    message = response.choices[0].message

    tool_calls = []

    for call in message.tool_calls or []:
        tool_calls.append(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=json.loads(call.function.arguments),
            )
        )

    return LLMResponse(
        text=message.content or "",
        tool_calls=tool_calls,
    )



def normalize_anthropic_response(response) -> LLMResponse:
    """Convert native Anthropic content blocks into our response format."""

    text_parts = []
    tool_calls = []

    for block in response.content:

        if block.type == "text":
            text_parts.append(block.text)

        elif block.type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                )
            )

    return LLMResponse(
        text="\n".join(text_parts),
        tool_calls=tool_calls,
    )



class AnthropicClient:
    """Adapter for Anthropic's native Messages API."""

    def __init__(self, client, model: str, max_tokens: int = 8192):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

        self._system = ""
        self._messages = []
        self._last_assistant_content = None

    def _request(self, tools=None) -> LLMResponse:
        """Send a request and normalize the native response."""

        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._messages,
        }

        if self._system:
            kwargs["system"] = self._system

        if tools is not None:
            kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)

        # Detect an incomplete response before normalizing it.
        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Anthropic reached max_tokens={self.max_tokens} "
                f"before completing its response. "
                f"Output tokens: {response.usage.output_tokens}"
            )

        normalized = normalize_anthropic_response(response)

        # A response without text or tool calls is not a useful result.
        if not normalized.text.strip() and not normalized.tool_calls:
            raise RuntimeError(
                "Anthropic returned neither visible text nor tool calls. "
                f"Stop reason: {response.stop_reason}"
            )

        # Preserve the original blocks for tool-result continuation.
        self._last_assistant_content = response.content

        # Preserve original content blocks for tool-result continuation.
        self._last_assistant_content = response.content
        print(
            "\n[ANTHROPIC DEBUG]",
            f"stop_reason={response.stop_reason}",
            f"block_types={[block.type for block in response.content]}",
            f"input_tokens={response.usage.input_tokens}",
            f"output_tokens={response.usage.output_tokens}",
        )
        return normalized

    def chat(self, messages, tools=None) -> LLMResponse:
        """Start a new conversation."""

        # Anthropic expects the system prompt separately.
        self._system = "\n\n".join(
            message["content"]
            for message in messages
            if message["role"] == "system"
        )

        self._messages = [
            message.copy()
            for message in messages
            if message["role"] != "system"
        ]

        self._last_assistant_content = None

        return self._request(tools=tools)

    def continue_with_tool_results(
        self,
        assistant_response: LLMResponse,
        tool_results: list,
        tools=None,
    ) -> LLMResponse:
        """Continue after executing the requested Python tools."""

        if self._last_assistant_content is None:
            raise RuntimeError("No previous assistant response.")

        if len(tool_results) != len(assistant_response.tool_calls):
            raise ValueError(
                "Each tool call must have exactly one tool result."
            )

        # Preserve Claude's original assistant message.
        self._messages.append({
            "role": "assistant",
            "content": self._last_assistant_content,
        })

        # Convert Python tool results to Anthropic tool_result blocks.
        result_blocks = []

        for call, result in zip(
            assistant_response.tool_calls,
            tool_results,
        ):
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": json.dumps(result),
            })

        self._messages.append({
            "role": "user",
            "content": result_blocks,
        })

        return self._request(tools=tools)



def get_llm_client(provider: str | None = None):
    """Build the appropriate adapter from provider configuration."""

    from anthropic import Anthropic
    from openai import OpenAI
    from src.config import LLM_PROVIDER, get_provider_config

    selected = provider or LLM_PROVIDER
    config = get_provider_config(selected)

    if config["adapter"] == "anthropic":
        return AnthropicClient(
            client=Anthropic(api_key=config["api_key"]),
            model=config["model"],
        )

    if config["adapter"] == "openai_compatible":
        return OpenAICompatibleClient(
            client=OpenAI(
                api_key=config["api_key"],
                base_url=config["base_url"],
            ),
            model=config["model"],
        )

    raise ValueError(
        f"Unsupported adapter: {config['adapter']}"
    )
