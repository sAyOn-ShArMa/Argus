from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from argus.ai.provider import ToolCall
from argus.config import ApplicationConfig, ToolsConfig
from argus.tools.computer import build_computer_tool_runtime


class ComputerToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = ToolsConfig(
            enabled=True,
            max_rounds=4,
            allowed_roots=(self.root,),
            applications=(ApplicationConfig("notepad", "notepad.exe"),),
            allowed_commands=("whoami.exe",),
        )

    def test_file_search_stays_in_approved_root_and_skips_venv(self) -> None:
        wanted = self.root / "robotics_notes.txt"
        wanted.write_text("notes", encoding="utf-8")
        ignored_directory = self.root / ".venv"
        ignored_directory.mkdir()
        (ignored_directory / "robotics_secret.txt").write_text(
            "ignored", encoding="utf-8"
        )
        runtime = build_computer_tool_runtime(self.config)

        result = json.loads(
            runtime.execute(
                ToolCall("1", "find_files", '{"query":"robotics","max_results":10}')
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["matches"], [str(wanted)])

    def test_open_application_uses_exact_allowlisted_executable_without_shell(self) -> None:
        runtime = build_computer_tool_runtime(self.config)
        with patch(
            "argus.tools.computer.subprocess.Popen",
            return_value=SimpleNamespace(pid=42),
        ) as launch:
            result = json.loads(
                runtime.execute(
                    ToolCall("1", "open_application", '{"application":"notepad"}')
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(launch.call_args.args[0], ["notepad.exe"])
        self.assertFalse(launch.call_args.kwargs["shell"])

    def test_open_website_rejects_non_web_scheme(self) -> None:
        runtime = build_computer_tool_runtime(self.config)
        with patch("argus.tools.computer.webbrowser.open") as open_browser:
            result = json.loads(
                runtime.execute(
                    ToolCall("1", "open_website", '{"url":"file:///C:/private.txt"}')
                )
            )

        self.assertFalse(result["ok"])
        open_browser.assert_not_called()

    def test_open_website_accepts_any_public_http_or_https_site(self) -> None:
        runtime = build_computer_tool_runtime(self.config)
        public_answer = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with (
            patch(
                "argus.tools.web.socket.getaddrinfo",
                return_value=public_answer,
            ),
            patch(
                "argus.tools.computer.webbrowser.open",
                return_value=True,
            ) as open_browser,
        ):
            result = json.loads(
                runtime.execute(
                    ToolCall(
                        "1",
                        "open_website",
                        '{"url":"https://example.com/somewhere"}',
                    )
                )
            )

        self.assertTrue(result["ok"])
        open_browser.assert_called_once_with(
            "https://example.com/somewhere", new=2
        )

    def test_media_control_is_limited_to_fixed_keys(self) -> None:
        runtime = build_computer_tool_runtime(self.config)
        with (
            patch("argus.tools.computer.platform.system", return_value="Windows"),
            patch("argus.tools.computer._send_windows_media_key") as send_key,
        ):
            accepted = json.loads(
                runtime.execute(
                    ToolCall("1", "media_control", '{"action":"play_pause"}')
                )
            )
            rejected = json.loads(
                runtime.execute(
                    ToolCall("2", "media_control", '{"action":"power_off"}')
                )
            )

        self.assertTrue(accepted["ok"])
        self.assertFalse(rejected["ok"])
        send_key.assert_called_once_with(0xB3)

    def test_denied_command_never_starts_a_process(self) -> None:
        runtime = build_computer_tool_runtime(
            self.config, confirmer=lambda definition, arguments: False
        )
        with patch("argus.tools.computer.subprocess.run") as run:
            result = json.loads(
                runtime.execute(
                    ToolCall("1", "run_command", '{"program":"whoami.exe"}')
                )
            )

        self.assertEqual(result["status"], "denied")
        run.assert_not_called()

    def test_confirmed_command_uses_argument_list_and_no_shell(self) -> None:
        runtime = build_computer_tool_runtime(
            self.config, confirmer=lambda definition, arguments: True
        )
        completed = subprocess.CompletedProcess(
            ["whoami.exe", "/user"], 0, "account", ""
        )
        with (
            patch(
                "argus.tools.computer._resolve_command",
                return_value=r"C:\\Windows\\System32\\whoami.exe",
            ),
            patch("argus.tools.computer.subprocess.run", return_value=completed) as run,
        ):
            result = json.loads(
                runtime.execute(
                    ToolCall(
                        "1",
                        "run_command",
                        '{"program":"whoami.exe","arguments":["/user"]}',
                    )
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            run.call_args.args[0],
            [r"C:\\Windows\\System32\\whoami.exe", "/user"],
        )
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)


if __name__ == "__main__":
    unittest.main()
