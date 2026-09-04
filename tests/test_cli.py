from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from argus.cli import (
    _console_confirmer,
    _handle_memory_action,
    _run_voice_turn,
    _run_wake_mode,
    _session_api_key,
)
from argus.commands import CommandResult
from argus.core import Agent
from argus.memory import LocalMemoryStore
from argus.tools.runtime import ToolDefinition
from argus.voice import NoSpeechDetected


class SessionKeyTests(unittest.TestCase):
    def test_uses_universal_environment_key_first(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "ARGUS_API_KEY": "universal-key",
                    "GROQ_API_KEY": "legacy-key",
                },
                clear=True,
            ),
            patch("argus.cli.getpass.getpass") as prompt,
        ):
            key = _session_api_key("groq")

        self.assertEqual(key, "universal-key")
        prompt.assert_not_called()

    def test_uses_environment_key_without_prompting(self) -> None:
        with (
            patch.dict("os.environ", {"GROQ_API_KEY": "environment-key"}, clear=True),
            patch("argus.cli.getpass.getpass") as prompt,
        ):
            key = _session_api_key("groq")

        self.assertEqual(key, "environment-key")
        prompt.assert_not_called()

    def test_prompts_with_hidden_input_when_key_is_missing(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("argus.cli.getpass.getpass", return_value="  prompted-key  "),
            patch("builtins.print"),
        ):
            key = _session_api_key("groq")

        self.assertEqual(key, "prompted-key")

    def test_uses_configured_environment_name_for_compatible_provider(self) -> None:
        with (
            patch.dict("os.environ", {"COMPANY_API_KEY": "company-key"}, clear=True),
            patch("argus.cli.getpass.getpass") as prompt,
        ):
            key = _session_api_key("openai_compatible", "COMPANY_API_KEY")

        self.assertEqual(key, "company-key")
        prompt.assert_not_called()

    def test_confirmation_accepts_only_full_yes(self) -> None:
        definition = ToolDefinition(
            "run_command",
            "Run command",
            {"type": "object"},
            lambda arguments: {},
            confirmation="always",
        )
        confirmer = _console_confirmer("Argus")
        with (
            patch("builtins.input", side_effect=["y", "YES"]),
            patch("builtins.print"),
        ):
            self.assertFalse(confirmer(definition, {"program": "whoami.exe"}))
            self.assertTrue(confirmer(definition, {"program": "whoami.exe"}))


class PermanentDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = LocalMemoryStore(
            Path(self.temporary.name) / "argus.db",
            profile_id="owner",
            profile_name="Owner",
        )

        class Provider:
            name = "test"
            model = "test-model"

            def stream_reply(self, *, messages, system_prompt):
                yield "ok"

        self.agent = Agent(Provider(), "You are Argus.")

    def test_denied_permanent_deletion_keeps_record(self) -> None:
        memory = self.store.add_memory("Keep me")
        command = CommandResult(
            True, action="delete_memory", payload=memory.id
        )

        with patch("builtins.input", return_value="no"), patch("builtins.print"):
            message = _handle_memory_action(
                command,
                store=self.store,
                agent=self.agent,
                assistant_name="Argus",
            )

        self.assertEqual(message, "Deletion cancelled.")
        self.assertEqual(len(self.store.list_memories()), 1)

    def test_full_yes_permanently_deletes_record(self) -> None:
        memory = self.store.add_memory("Delete me")
        command = CommandResult(
            True, action="delete_memory", payload=memory.id
        )

        with patch("builtins.input", return_value="YES"), patch("builtins.print"):
            message = _handle_memory_action(
                command,
                store=self.store,
                agent=self.agent,
                assistant_name="Argus",
            )

        self.assertIn("permanently deleted", message)
        self.assertEqual(self.store.list_memories(), [])


class VoiceTurnTests(unittest.TestCase):
    def test_voice_transcript_uses_normal_agent_path_and_speaks_reply(self) -> None:
        class Provider:
            name = "test"
            model = "test-model"

            def stream_reply(self, *, messages, system_prompt):
                yield "Ready"
                yield ", sir."

        class Voice:
            def __init__(self) -> None:
                self.spoken: list[str] = []

            def listen(self) -> str:
                return "Check my project"

            def speak(self, text: str) -> None:
                self.spoken.append(text)

        agent = Agent(Provider(), "You are Argus.")
        voice = Voice()

        with patch("builtins.print"):
            _run_voice_turn(voice, agent, "Argus", 20)  # type: ignore[arg-type]

        self.assertEqual(voice.spoken, ["Ready, sir."])
        self.assertEqual(agent.history[0]["content"], "Check my project")

    def test_wake_mode_processes_command_then_accepts_spoken_stop(self) -> None:
        class Provider:
            name = "test"
            model = "test-model"

            def stream_reply(self, *, messages, system_prompt):
                yield "All systems nominal."

        class Wake:
            phrase = "argus"
            command_attempts = 2

            def __init__(self) -> None:
                self.transcripts = iter(["system status", "stop wake mode"])
                self.wait_count = 0
                self.acknowledged = 0
                self.spoken: list[str] = []

            def wait(self) -> None:
                self.wait_count += 1

            def acknowledge(self) -> None:
                self.acknowledged += 1

            def listen_for_command(self) -> str:
                return next(self.transcripts)

            def speak(self, text: str) -> None:
                self.spoken.append(text)

        agent = Agent(Provider(), "You are Argus.")
        wake = Wake()

        with patch("builtins.print"):
            _run_wake_mode(wake, agent, "Argus")  # type: ignore[arg-type]

        self.assertEqual(wake.wait_count, 2)
        self.assertEqual(wake.acknowledged, 2)
        self.assertEqual(wake.spoken, ["All systems nominal."])

    def test_wake_mode_retries_once_when_command_is_not_heard(self) -> None:
        class Provider:
            name = "test"
            model = "test-model"

            def stream_reply(self, *, messages, system_prompt):
                yield "unused"

        class Wake:
            phrase = "argus"
            command_attempts = 2

            def __init__(self) -> None:
                self.attempt = 0
                self.spoken: list[str] = []

            def wait(self) -> None:
                pass

            def acknowledge(self) -> None:
                pass

            def listen_for_command(self) -> str:
                self.attempt += 1
                if self.attempt == 1:
                    raise NoSpeechDetected("nothing heard")
                return "stop wake mode"

            def speak(self, text: str) -> None:
                self.spoken.append(text)

        wake = Wake()
        with patch("builtins.print"):
            _run_wake_mode(
                wake, Agent(Provider(), "You are Argus."), "Argus"  # type: ignore[arg-type]
            )

        self.assertEqual(wake.attempt, 2)
        self.assertEqual(
            wake.spoken, ["I didn't catch that. Please say the command again."]
        )


if __name__ == "__main__":
    unittest.main()
