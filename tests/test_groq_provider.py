from __future__ import annotations

from types import SimpleNamespace
import unittest

from argus.ai.groq_provider import GroqProvider
from argus.ai.provider import ProviderError, ToolCall


class FakeCompletions:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self.chunks = chunks
        self.request: dict[str, object] | None = None

    def create(self, **kwargs: object) -> list[SimpleNamespace]:
        self.request = kwargs
        return self.chunks


class SequencedCompletions:
    def __init__(self, responses: list[list[SimpleNamespace]]) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> list[SimpleNamespace]:
        self.requests.append(kwargs)
        return next(self._responses)


def make_provider(chunks: list[SimpleNamespace]) -> tuple[GroqProvider, FakeCompletions]:
    completions = FakeCompletions(chunks)
    provider = object.__new__(GroqProvider)
    provider._name = "groq"
    provider._model = "test-model"
    provider._temperature = 0.3
    provider._max_completion_tokens = 256
    provider._secret = "test-secret"
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return provider, completions


class GroqProviderTests(unittest.TestCase):
    def test_rejects_a_launch_command_pasted_as_the_key(self) -> None:
        with self.assertRaisesRegex(ProviderError, "not a command or file path"):
            GroqProvider(
                api_key=r".\.venv\Scripts\python.exe -m argus",
                model="test-model",
                temperature=0.3,
                max_completion_tokens=256,
            )

    def test_streams_deltas_and_sends_system_plus_history(self) -> None:
        chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=" there"))]
            ),
        ]
        provider, completions = make_provider(chunks)

        fragments = list(
            provider.stream_reply(
                messages=[{"role": "user", "content": "Hi"}],
                system_prompt="You are Argus.",
            )
        )

        self.assertEqual(fragments, ["Hello", " there"])
        self.assertEqual(
            completions.request["messages"],  # type: ignore[index]
            [
                {"role": "system", "content": "You are Argus."},
                {"role": "user", "content": "Hi"},
            ],
        )
        self.assertTrue(completions.request["stream"])  # type: ignore[index]

    def test_sdk_error_redacts_key(self) -> None:
        provider, _ = make_provider([])

        class BrokenCompletions:
            def create(self, **kwargs):
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

    def test_executes_streamed_tool_call_then_returns_final_text(self) -> None:
        first_response = [
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
        final_response = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="Windows is ready.")
                    )
                ]
            )
        ]
        completions = SequencedCompletions([first_response, final_response])
        provider, _ = make_provider([])
        provider._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        calls: list[ToolCall] = []

        def execute(call: ToolCall) -> str:
            calls.append(call)
            return '{"ok": true, "system": "Windows"}'

        fragments = list(
            provider.stream_reply(
                messages=[{"role": "user", "content": "Check the system."}],
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
        second_messages = completions.requests[1]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "call-1")
        self.assertTrue(completions.requests[0]["parallel_tool_calls"])

    def test_stops_repeated_tool_calls_at_configured_limit(self) -> None:
        tool_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-1",
                                function=SimpleNamespace(name="loop", arguments="{}"),
                            )
                        ],
                    )
                )
            ]
        )
        completions = SequencedCompletions([[tool_chunk], [tool_chunk]])
        provider, _ = make_provider([])
        provider._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        with self.assertRaisesRegex(ProviderError, "1-round tool limit"):
            list(
                provider.stream_reply(
                    messages=[{"role": "user", "content": "Loop"}],
                    system_prompt="You are Argus.",
                    tools=[{"type": "function", "function": {"name": "loop"}}],
                    execute_tool=lambda call: "{}",
                    max_tool_rounds=1,
                )
            )


if __name__ == "__main__":
    unittest.main()
