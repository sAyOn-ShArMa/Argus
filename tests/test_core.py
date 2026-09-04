from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
import tempfile
import unittest

from argus.ai.provider import Message, ProviderError, ToolCall
from argus.core import Agent, AgentUnavailable
from argus.memory import LocalMemoryStore
from argus.tools.runtime import ToolDefinition, ToolRuntime


class RecordingProvider:
    name = "test"
    model = "test-model"

    def __init__(self, replies: list[list[str]]) -> None:
        self._replies = iter(replies)
        self.calls: list[tuple[list[Message], str]] = []

    def stream_reply(
        self,
        *,
        messages: Sequence[Message],
        system_prompt: str,
    ) -> Iterator[str]:
        self.calls.append(([message.copy() for message in messages], system_prompt))
        yield from next(self._replies)


class FailingProvider:
    name = "test"
    model = "test-model"

    def stream_reply(
        self,
        *,
        messages: Sequence[Message],
        system_prompt: str,
    ) -> Iterator[str]:
        yield "partial"
        raise ProviderError("temporary failure")


class ToolAwareProvider:
    name = "test"
    model = "test-model"

    def __init__(self) -> None:
        self.tool_names: list[str] = []

    def stream_reply(
        self,
        *,
        messages,
        system_prompt,
        tools=(),
        execute_tool=None,
        max_tool_rounds=6,
    ):
        self.tool_names = [tool["function"]["name"] for tool in tools]
        result = execute_tool(ToolCall("1", "demo", "{}"))
        self.asserted_result = result
        yield "Tool complete"


class ReceiptAwareProvider:
    name = "test"
    model = "test-model"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.calls = 0

    def stream_reply(
        self,
        *,
        messages,
        system_prompt,
        tools=(),
        execute_tool=None,
        max_tool_rounds=6,
    ):
        self.prompts.append(system_prompt)
        if self.calls == 0:
            execute_tool(ToolCall("1", "open_application", "{}"))
        self.calls += 1
        yield "Done"


class AgentTests(unittest.TestCase):
    def test_streams_and_commits_complete_reply(self) -> None:
        agent = Agent(RecordingProvider([["Hello", " there"]]), "You are Argus.")

        self.assertEqual(list(agent.stream_turn("Hi")), ["Hello", " there"])
        self.assertEqual(
            agent.history,
            (
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello there"},
            ),
        )

    def test_third_request_contains_all_previous_turns(self) -> None:
        provider = RecordingProvider([["One"], ["Two"], ["Helios"]])
        agent = Agent(provider, "You are Argus.")

        list(agent.stream_turn("My project is Helios."))
        list(agent.stream_turn("Give a planning tip."))
        list(agent.stream_turn("What is my project?"))

        third_messages, _ = provider.calls[2]
        self.assertEqual(third_messages[0]["content"], "My project is Helios.")
        self.assertEqual(len(third_messages), 5)

    def test_provider_failure_rolls_back_partial_turn(self) -> None:
        agent = Agent(FailingProvider(), "You are Argus.")

        with self.assertRaisesRegex(AgentUnavailable, "temporary failure"):
            list(agent.stream_turn("Hello"))

        self.assertEqual(agent.history, ())

    def test_empty_provider_response_is_clean_failure(self) -> None:
        agent = Agent(RecordingProvider([[]]), "You are Argus.")

        with self.assertRaisesRegex(AgentUnavailable, "returned no text"):
            list(agent.stream_turn("Hello"))

        self.assertEqual(agent.history, ())

    def test_reset_clears_history(self) -> None:
        agent = Agent(RecordingProvider([["Noted"]]), "You are Argus.")
        list(agent.stream_turn("Remember this"))

        agent.reset()

        self.assertEqual(agent.history, ())

    def test_passes_provider_neutral_tool_runtime_to_provider(self) -> None:
        provider = ToolAwareProvider()
        runtime = ToolRuntime(
            [
                ToolDefinition(
                    "demo",
                    "Demo tool",
                    {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    lambda arguments: {"done": True},
                )
            ],
            max_rounds=3,
        )
        agent = Agent(provider, "You are Argus.", tool_runtime=runtime)

        self.assertEqual(list(agent.stream_turn("Use the demo")), ["Tool complete"])
        self.assertEqual(provider.tool_names, ["demo"])
        self.assertIn('"ok": true', provider.asserted_result)

    def test_verified_tool_receipt_is_injected_into_later_system_context(self) -> None:
        provider = ReceiptAwareProvider()
        runtime = ToolRuntime(
            [
                ToolDefinition(
                    "open_application",
                    "Open an approved app",
                    {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    lambda arguments: {"started": True},
                )
            ]
        )
        agent = Agent(provider, "You are Argus.", tool_runtime=runtime)

        list(agent.stream_turn("Open Notepad"))
        list(agent.stream_turn("Open Calculator"))

        self.assertNotIn("VERIFIED LOCAL TOOL RECEIPTS", provider.prompts[0])
        self.assertIn(
            "open_application completed successfully", provider.prompts[1]
        )
        self.assertIn("Never deny that these actions succeeded", provider.prompts[1])

    def test_completed_turn_is_loaded_by_a_new_agent_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalMemoryStore(
                Path(temporary) / "argus.db",
                profile_id="owner",
                profile_name="Owner",
            )
            first = Agent(
                RecordingProvider([["Stored"]]),
                "You are Argus.",
                conversation_store=store,
            )
            list(first.stream_turn("My project is Helios."))

            second = Agent(
                RecordingProvider([["Helios"]]),
                "You are Argus.",
                conversation_store=store,
            )

            self.assertEqual(second.history, first.history)

    def test_durable_reset_survives_a_new_agent_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalMemoryStore(
                Path(temporary) / "argus.db",
                profile_id="owner",
                profile_name="Owner",
            )
            agent = Agent(
                RecordingProvider([["Stored"]]),
                "You are Argus.",
                conversation_store=store,
            )
            list(agent.stream_turn("Old context"))
            agent.reset()

            restarted = Agent(
                RecordingProvider([["Fresh"]]),
                "You are Argus.",
                conversation_store=store,
            )

            self.assertEqual(restarted.history, ())


if __name__ == "__main__":
    unittest.main()
