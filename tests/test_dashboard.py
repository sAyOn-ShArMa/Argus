from __future__ import annotations

from types import SimpleNamespace
import unittest

from argus.dashboard import DashboardError, DashboardSession
from argus.dashboard.app import DashboardWindow
from argus.voice import VoiceError


class FakeAgent:
    provider_name = "groq"
    model_name = "openai/gpt-oss-120b"
    tool_descriptions = (
        "open_application (low risk)",
        "run_command (confirmation required)",
    )

    def __init__(self) -> None:
        self.calls: list[str] = []

    def stream_turn(self, message: str):
        self.calls.append(message)
        yield "A calm "
        yield "reply."


class FakeMemory:
    def list_notifications(self, limit: int):
        self.limit = limit
        return [
            SimpleNamespace(
                id=4,
                priority="high",
                category="deadline",
                content="Competition deadline soon.",
                delivered_at="2026-09-04T12:00:00+05:45",
            )
        ]


class FakeRobotics:
    def __init__(self) -> None:
        self.closed = False

    def list_devices(self):
        return (
            SimpleNamespace(
                device_id="sim_robot",
                name="Simulator",
                transport="simulator",
                actuators_enabled=True,
            ),
        )

    def close(self) -> None:
        self.closed = True


class FakeVoice:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def listen(self) -> str:
        return "Open Notepad"

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class FakeWakeDetector:
    def __init__(self) -> None:
        self.waited = 0

    def wait(self) -> None:
        self.waited += 1


class DashboardSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = FakeAgent()
        self.memory = FakeMemory()
        self.robotics = FakeRobotics()
        self.voice = FakeVoice()
        self.wake = FakeWakeDetector()
        self.session = DashboardSession(
            self.agent,  # type: ignore[arg-type]
            profile_id="owner",
            notification_limit=20,
            memory_store=self.memory,  # type: ignore[arg-type]
            robotics_service=self.robotics,  # type: ignore[arg-type]
            voice_factory=lambda stop_requested: self.voice,  # type: ignore[arg-type,return-value]
            wake_detector_factory=lambda: self.wake,
        )

    def test_refresh_reports_local_on_demand_state(self) -> None:
        snapshot = self.session.refresh()

        self.assertEqual(snapshot.profile_id, "owner")
        self.assertEqual(snapshot.model, "openai/gpt-oss-120b")
        self.assertFalse(snapshot.proactive_enabled)
        self.assertTrue(snapshot.local_actions_enabled)
        self.assertEqual(snapshot.tool_count, 2)
        self.assertEqual(snapshot.devices[0].device_id, "sim_robot")
        self.assertEqual(snapshot.notifications[0].notification_id, 4)
        self.assertEqual(self.memory.limit, 20)

    def test_chat_is_bounded_and_uses_local_agent(self) -> None:
        self.assertEqual(self.session.send_message("Hello"), "A calm reply.")
        self.assertEqual(self.agent.calls, ["Hello"])

        with self.assertRaisesRegex(DashboardError, "1 to 4000"):
            self.session.send_message("   ")
        with self.assertRaisesRegex(DashboardError, "1 to 4000"):
            self.session.send_message("x" * 4_001)

    def test_voice_turn_uses_same_local_agent_and_speaks(self) -> None:
        result = self.session.voice_turn()

        self.assertEqual(result.transcript, "Open Notepad")
        self.assertEqual(result.reply, "A calm reply.")
        self.assertEqual(self.agent.calls, ["Open Notepad"])
        self.assertEqual(self.voice.spoken, ["A calm reply."])

    def test_leaving_voice_mode_cancels_capture_until_mode_restarts(self) -> None:
        self.session.end_voice_mode()

        with self.assertRaisesRegex(VoiceError, "cancelled"):
            self.session.voice_turn()

        self.session.begin_voice_mode()
        self.assertEqual(self.session.voice_turn().transcript, "Open Notepad")

    def test_idle_wake_detector_accepts_return(self) -> None:
        self.session.wait_for_return_phrase()
        self.assertEqual(self.wake.waited, 1)

    def test_close_releases_local_device_service(self) -> None:
        self.session.close()
        self.assertTrue(self.robotics.closed)


class DashboardWindowCommandTests(unittest.TestCase):
    def test_disconnect_text_enters_idle_lock_without_calling_model(self) -> None:
        class MessageBox:
            def get(self, start: str, end: str) -> str:
                return "disconnect"

            def delete(self, start: str, end: str) -> None:
                pass

        window = DashboardWindow.__new__(DashboardWindow)
        window._message = MessageBox()  # type: ignore[attr-defined]
        window._locked = False
        locked: list[bool] = []
        window._enter_idle_lock = lambda: locked.append(True)  # type: ignore[method-assign]

        window.send_message()

        self.assertEqual(locked, [True])


if __name__ == "__main__":
    unittest.main()
