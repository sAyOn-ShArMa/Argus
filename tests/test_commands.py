from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from argus.commands import CommandRouter
from argus.config import (
    AIConfig,
    AppConfig,
    AssistantConfig,
    DashboardConfig,
    ProactiveConfig,
    RoboticsConfig,
    RoboticsDeviceConfig,
    ServerClientConfig,
    ServerConfig,
    VisionConfig,
    VoiceConfig,
    WakeConfig,
)
from argus.core import Agent
from argus.memory import LocalMemoryStore


class EmptyProvider:
    name = "test"
    model = "test-model"

    def stream_reply(self, *, messages, system_prompt):
        yield "ok"


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = Agent(EmptyProvider(), "You are Argus.")
        self.config = AppConfig(
            assistant=AssistantConfig("Argus", "Help."),
            ai=AIConfig("test", "test-model", 0.3, 100),
            source=None,  # type: ignore[arg-type]
        )
        moment = datetime(2026, 8, 15, 9, 30, tzinfo=timezone.utc)
        self.router = CommandRouter(now=lambda: moment)

    def test_noncommand_is_not_handled(self) -> None:
        self.assertFalse(
            self.router.route("hello", agent=self.agent, config=self.config).handled
        )

    def test_help_lists_commands(self) -> None:
        result = self.router.route("/help", agent=self.agent, config=self.config)
        self.assertTrue(result.handled)
        self.assertIn("/status", result.message or "")
        self.assertNotIn("/server-status", result.message or "")

    def test_server_status_does_not_start_or_expose_credentials(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            server=ServerConfig(
                enabled=True,
                clients=(
                    ServerClientConfig(
                        "owner", "owner", "Owner", "owner", "SECRET_TOKEN_ENV"
                    ),
                ),
            ),
        )

        result = self.router.route("/server-status", agent=self.agent, config=config)

        self.assertIn("removed", result.message or "")
        self.assertIn("local-only", result.message or "")
        self.assertNotIn("SECRET_TOKEN_ENV", result.message or "")

    def test_dashboard_status_reports_separate_safe_process(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            dashboard=DashboardConfig(enabled=True),
        )

        result = self.router.route(
            "/dashboard-status", agent=self.agent, config=config
        )

        self.assertIn("starts separately", result.message or "")
        self.assertIn("no remote endpoint", result.message or "")
        self.assertIn("locks after 120 seconds", result.message or "")

    def test_time_is_local_command(self) -> None:
        result = self.router.route("/time", agent=self.agent, config=self.config)
        self.assertIn("09:30 AM", result.message or "")

    def test_tools_reports_disabled_state(self) -> None:
        result = self.router.route("/tools", agent=self.agent, config=self.config)
        self.assertEqual(result.message, "Computer tools are disabled.")

    def test_voice_reports_disabled_state(self) -> None:
        result = self.router.route("/voice", agent=self.agent, config=self.config)
        self.assertIn("disabled", result.message or "")

    def test_voice_requests_one_explicit_turn_when_enabled(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            voice=VoiceConfig(enabled=True),
        )

        result = self.router.route("/voice", agent=self.agent, config=config)

        self.assertEqual(result.action, "voice_turn")
        self.assertIsNone(result.message)

    def test_wake_requests_visible_mode_when_enabled(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            voice=VoiceConfig(enabled=True),
            wake=WakeConfig(enabled=True),
        )

        result = self.router.route("/wake", agent=self.agent, config=config)

        self.assertEqual(result.action, "wake_mode")

    def test_reset_clears_context(self) -> None:
        list(self.agent.stream_turn("Remember"))
        result = self.router.route("/reset", agent=self.agent, config=self.config)
        self.assertEqual(self.agent.history, ())
        self.assertIn("cleared", result.message or "")

    def test_exit_requests_clean_shutdown(self) -> None:
        result = self.router.route("/exit", agent=self.agent, config=self.config)
        self.assertTrue(result.should_exit)

    def test_camera_requests_one_explicit_frame_when_vision_is_enabled(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            vision=VisionConfig(enabled=True),
        )

        result = self.router.route("/camera", agent=self.agent, config=config)

        self.assertEqual(result.action, "camera_once")

    def test_vision_command_preserves_image_path_as_action_payload(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            vision=VisionConfig(enabled=True),
        )

        result = self.router.route(
            "/vision C:\\Photos\\robot image.png",
            agent=self.agent,
            config=config,
        )

        self.assertEqual(result.action, "analyze_image")
        self.assertEqual(result.payload, "C:\\Photos\\robot image.png")

    def test_robotics_commands_target_only_configured_device(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            robotics=RoboticsConfig(
                enabled=True,
                devices=(
                    RoboticsDeviceConfig(
                        device_id="sim_robot",
                        name="Simulator",
                        actuators_enabled=True,
                        allowed_actuators=("servo",),
                    ),
                ),
            ),
        )

        telemetry = self.router.route(
            "/telemetry sim_robot", agent=self.agent, config=config
        )
        actuation = self.router.route(
            "/actuate sim_robot servo 90", agent=self.agent, config=config
        )

        self.assertEqual(telemetry.action, "device_telemetry")
        self.assertEqual(telemetry.payload.device_id, "sim_robot")  # type: ignore[union-attr]
        self.assertEqual(actuation.action, "device_actuate")
        self.assertEqual(actuation.payload.value, 90)  # type: ignore[union-attr]
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            self.router.route(
                "/device-status other", agent=self.agent, config=config
            )


class MemoryCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = LocalMemoryStore(
            Path(self.temporary.name) / "argus.db",
            profile_id="owner",
            profile_name="Owner",
        )
        self.agent = Agent(EmptyProvider(), "You are Argus.")
        self.config = AppConfig(
            assistant=AssistantConfig("Argus", "Help."),
            ai=AIConfig("test", "test-model", 0.3, 100),
            source=None,  # type: ignore[arg-type]
        )
        self.router = CommandRouter(memory_store=self.store)

    def test_explicit_remember_and_list_commands(self) -> None:
        added = self.router.route(
            "/remember My project is Helios", agent=self.agent, config=self.config
        )
        listed = self.router.route(
            "/memories", agent=self.agent, config=self.config
        )

        self.assertIn("stored", (added.message or "").casefold())
        self.assertIn("Helios", listed.message or "")

    def test_permanent_memory_deletion_is_returned_as_confirmation_action(self) -> None:
        record = self.store.add_memory("Delete me")

        result = self.router.route(
            f"/forget {record.id}", agent=self.agent, config=self.config
        )

        self.assertEqual(result.action, "delete_memory")
        self.assertEqual(result.payload, record.id)
        self.assertEqual(len(self.store.list_memories()), 1)

    def test_task_and_reminder_commands_store_structured_records(self) -> None:
        self.router.route(
            "/task add Test the robot", agent=self.agent, config=self.config
        )
        self.router.route(
            "/remind 2026-08-16 09:00 | Charge the robot",
            agent=self.agent,
            config=self.config,
        )

        self.assertEqual(self.store.list_tasks()[0].title, "Test the robot")
        self.assertEqual(
            self.store.list_reminders()[0].content, "Charge the robot"
        )

    def test_clear_history_requests_confirmation_without_deleting(self) -> None:
        self.store.append_turn("Hello", "Hi")

        result = self.router.route(
            "/clear-history", agent=self.agent, config=self.config
        )

        self.assertEqual(result.action, "clear_history")
        self.assertEqual(len(self.store.list_conversation()), 2)

    def test_deadline_calendar_and_notification_commands(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            memory=self.config.memory,
            proactive=ProactiveConfig(
                enabled=True,
                quiet_hours_start="00:00",
                quiet_hours_end="00:00",
            ),
        )

        deadline = self.router.route(
            "/deadline 2026-08-16 09:00 | Finish robot",
            agent=self.agent,
            config=config,
        )
        event = self.router.route(
            "/event add 2026-08-16 10:00 | high | Robotics meeting",
            agent=self.agent,
            config=config,
        )
        check = self.router.route(
            "/check-alerts", agent=self.agent, config=config
        )

        self.assertIn("Deadline", deadline.message or "")
        self.assertIn("Calendar event", event.message or "")
        self.assertEqual(self.store.list_reminders()[0].category, "deadline")
        self.assertEqual(self.store.list_calendar_events()[0].priority, "high")
        self.assertEqual(check.action, "check_notifications")

    def test_calendar_deletion_is_a_confirmed_cli_action(self) -> None:
        event = self.store.add_calendar_event(
            "Delete me", "2026-08-16T10:00:00+00:00"
        )

        result = self.router.route(
            f"/event delete {event.id}", agent=self.agent, config=self.config
        )

        self.assertEqual(result.action, "delete_event")
        self.assertEqual(result.payload, event.id)

    def test_notification_status_and_clear_commands(self) -> None:
        config = AppConfig(
            assistant=self.config.assistant,
            ai=self.config.ai,
            source=self.config.source,
            proactive=ProactiveConfig(
                enabled=True,
                quiet_hours_start="00:00",
                quiet_hours_end="00:00",
            ),
        )

        status = self.router.route(
            "/notifications-status", agent=self.agent, config=config
        )
        clear = self.router.route(
            "/clear-notifications", agent=self.agent, config=config
        )

        self.assertIn("notifications allowed now", status.message or "")
        self.assertFalse(status.should_exit)
        self.assertEqual(clear.action, "clear_notifications")


if __name__ == "__main__":
    unittest.main()
