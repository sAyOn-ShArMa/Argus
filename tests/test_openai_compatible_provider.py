from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from argus.ai.openai_compatible_provider import OpenAICompatibleProvider
from argus.ai.provider import ProviderError, ToolCall


class SequencedCompletions:
    def __init__(self, responses: list[list[SimpleNamespace]]) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> list[SimpleNamespace]:
        self.requests.append(kwargs)
        return next(self._responses)


def make_provider(
    responses: list[list[SimpleNamespace]],
    *,
    native_tokens: bool = False,
) -> tuple[OpenAICompatibleProvider, SequencedCompletions]:
    completions = SequencedCompletions(responses)
    provider = object.__new__(OpenAICompatibleProvider)
    provider._name = "openai" if native_tokens else "openai_compatible"
    provider._model = "test-model"
    provider._temperature = 0.3
    provider._max_completion_tokens = 256
    provider._use_native_token_parameter = native_tokens
    provider._secret = "test-secret"
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return provider, completions


def text_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
    )


class OpenAICompatibleProviderTests(unittest.TestCase):
    def test_accepts_a_base64_style_company_key(self) -> None:
        client_factory = Mock(return_value=SimpleNamespace())
        fake_module = SimpleNamespace(OpenAI=client_factory)

        with patch.dict("sys.modules", {"openai": fake_module}):
            provider = OpenAICompatibleProvider(
                api_key="test/key+base64=",
                model="test-model",
                temperature=0.3,
                max_completion_tokens=256,
                base_url="https://api.company.example/v1",
            )

        self.assertEqual(provider.name, "openai_compatible")
        client_factory.assert_called_once_with(
            api_key="test/key+base64=",
            base_url="https://api.company.example/v1",
            max_retries=2,
            timeout=60.0,
        )

    def test_rejects_a_command_pasted_as_the_key(self) -> None:
        with self.assertRaisesRegex(ProviderError, "not a command or file path"):
            OpenAICompatibleProvider(
                api_key=r".\.venv\Scripts\python.exe -m argus",
                model="test-model",
                temperature=0.3,
                max_completion_tokens=256,
                base_url="https://api.company.example/v1",
            )

    def test_streams_text_with_compatible_max_tokens(self) -> None:
        provider, completions = make_provider([[text_chunk("Hello")]])

        fragments = list(
            provider.stream_reply(
                messages=[{"role": "user", "content": "Hi"}],
                system_prompt="You are Argus.",
            )
        )

        self.assertEqual(fragments, ["Hello"])
        request = completions.requests[0]
        self.assertEqual(request["max_tokens"], 256)
        self.assertNotIn("max_completion_tokens", request)

    def test_official_openai_uses_native_token_parameter(self) -> None:
        provider, completions = make_provider(
            [[text_chunk("Hello")]], native_tokens=True
        )

        list(
            provider.stream_reply(
                messages=[{"role": "user", "content": "Hi"}],
                system_prompt="You are Argus.",
            )
        )

        request = completions.requests[0]
        self.assertEqual(request["max_completion_tokens"], 256)
        self.assertNotIn("max_tokens", request)

    def test_executes_streamed_tool_call_then_returns_text(self) -> None:
        tool_response = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call-1",
                                    function=SimpleNamespace(
                                        name="get_", arguments="{"
                                    ),
                                )
                            ],
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id=None,
                                    function=SimpleNamespace(
                                        name="system_info", arguments="}"
                                    ),
                                )
                            ],
                        )
                    )
                ]
            ),
        ]
        provider, completions = make_provider(
            [tool_response, [text_chunk("Windows is ready.")]]
        )
        calls: list[ToolCall] = []

        def execute(call: ToolCall) -> str:
            calls.append(call)
            return '{"ok":true}'

        fragments = list(
            provider.stream_reply(
                messages=[{"role": "user", "content": "Check it."}],
                system_prompt="You are Argus.",
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_system_info",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                execute_tool=execute,
            )
        )

        self.assertEqual(fragments, ["Windows is ready."])
        self.assertEqual(calls, [ToolCall("call-1", "get_system_info", "{}")])
        self.assertNotIn("parallel_tool_calls", completions.requests[0])
        self.assertEqual(
            completions.requests[1]["messages"][-1]["tool_call_id"], "call-1"
        )

    def test_sdk_error_redacts_the_key(self) -> None:
        provider, _ = make_provider([])

        class BrokenCompletions:
            def create(self, **kwargs: object) -> list[SimpleNamespace]:
                raise RuntimeError("bad test-secret")

        provider._client = SimpleNamespace(
            chat=SimpleNamespace(completions=BrokenCompletions())
        )

        with self.assertRaisesRegex(ProviderError, r"bad \[redacted\]"):
            list(
                provider.stream_reply(
                    messages=[{"role": "user", "content": "Hi"}],
                    system_prompt="You are Argus.",
                )
            )


if __name__ == "__main__":
    unittest.main()
