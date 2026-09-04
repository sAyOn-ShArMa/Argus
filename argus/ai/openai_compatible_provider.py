"""Streaming adapter for OpenAI and compatible Chat Completions APIs."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from argus.ai.provider import (
    Message,
    ProviderError,
    ToolCall,
    ToolExecutor,
    ToolSchema,
)


@dataclass(slots=True)
class _PendingToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


class OpenAICompatibleProvider:
    """Use an explicitly configured OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        temperature: float,
        max_completion_tokens: int,
        base_url: str,
        provider_name: str = "openai_compatible",
        use_native_token_parameter: bool = False,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderError(
                f"An API key is required for the configured {provider_name} provider."
            )

        normalized_key = api_key.strip()
        if (
            any(character.isspace() for character in normalized_key)
            or "\\" in normalized_key
        ):
            raise ProviderError(
                "The provider credential does not look like a single API key. "
                "Paste only the key, not a command or file path."
            )

        try:
            from openai import OpenAI
        except ImportError as error:
            raise ProviderError(
                "The OpenAI SDK is not installed. Run: python -m pip install -e ."
            ) from error

        self._name = provider_name
        self._model = model
        self._temperature = temperature
        self._max_completion_tokens = max_completion_tokens
        self._use_native_token_parameter = use_native_token_parameter
        self._secret = normalized_key
        self._client: Any = OpenAI(
            api_key=self._secret,
            base_url=base_url,
            max_retries=2,
            timeout=60.0,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    def stream_reply(
        self,
        *,
        messages: Sequence[Message],
        system_prompt: str,
        tools: Sequence[ToolSchema] = (),
        execute_tool: ToolExecutor | None = None,
        max_tool_rounds: int = 6,
    ) -> Iterator[str]:
        if max_tool_rounds < 1:
            raise ProviderError("max_tool_rounds must be at least 1.")
        if tools and execute_tool is None:
            raise ProviderError("A tool executor is required when tools are enabled.")

        request_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *[dict(message) for message in messages],
        ]
        completed_tool_rounds = 0

        try:
            while True:
                request: dict[str, Any] = {
                    "messages": request_messages,
                    "model": self._model,
                    "temperature": self._temperature,
                    "stream": True,
                }
                token_parameter = (
                    "max_completion_tokens"
                    if self._use_native_token_parameter
                    else "max_tokens"
                )
                request[token_parameter] = self._max_completion_tokens
                if tools:
                    request.update(tools=list(tools), tool_choice="auto")

                stream = self._client.chat.completions.create(**request)
                text_fragments: list[str] = []
                pending_calls: dict[int, _PendingToolCall] = {}

                for chunk in stream:
                    if not getattr(chunk, "choices", None):
                        continue
                    delta = chunk.choices[0].delta
                    fragment = getattr(delta, "content", None)
                    if fragment:
                        text_fragments.append(fragment)

                    for streamed_call in getattr(delta, "tool_calls", None) or ():
                        index = getattr(streamed_call, "index", 0)
                        pending = pending_calls.setdefault(index, _PendingToolCall())
                        call_id = getattr(streamed_call, "id", None)
                        if call_id:
                            pending.id = call_id
                        function = getattr(streamed_call, "function", None)
                        if function is not None:
                            name = getattr(function, "name", None)
                            arguments = getattr(function, "arguments", None)
                            if name:
                                pending.name += name
                            if arguments:
                                pending.arguments += arguments

                if not pending_calls:
                    yield from text_fragments
                    return

                if completed_tool_rounds >= max_tool_rounds:
                    raise ProviderError(
                        f"The model exceeded the {max_tool_rounds}-round tool limit."
                    )

                tool_calls: list[ToolCall] = []
                for index in sorted(pending_calls):
                    pending = pending_calls[index]
                    if not pending.id or not pending.name:
                        raise ProviderError("The model returned an incomplete tool call.")
                    tool_calls.append(
                        ToolCall(
                            id=pending.id,
                            name=pending.name,
                            arguments=pending.arguments or "{}",
                        )
                    )

                request_messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(text_fragments) or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": call.arguments,
                                },
                            }
                            for call in tool_calls
                        ],
                    }
                )

                assert execute_tool is not None
                for call in tool_calls:
                    request_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": execute_tool(call),
                        }
                    )
                completed_tool_rounds += 1
        except ProviderError:
            raise
        except Exception as error:
            detail = str(error).replace(self._secret, "[redacted]").strip()
            message = f"{self._name} request failed ({type(error).__name__})"
            if detail:
                message = f"{message}: {detail}"
            raise ProviderError(message) from error
