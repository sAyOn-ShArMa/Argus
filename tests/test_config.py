from __future__ import annotations

import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from argus.config import ConfigError, load_config


VALID_CONFIG = {
    "assistant": {"name": "Argus", "purpose": "Help the user."},
    "ai": {
        "provider": "groq",
        "model": "test-model",
        "temperature": 0.3,
        "max_completion_tokens": 256,
    },
}


class ConfigTests(unittest.TestCase):
    def write_config(self, data: object) -> Path:
        temporary = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8", delete=False
        )
        with temporary:
            json.dump(data, temporary)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        return Path(temporary.name)

    def test_loads_valid_json_configuration(self) -> None:
        config = load_config(self.write_config(VALID_CONFIG))

        self.assertEqual(config.assistant.name, "Argus")
        self.assertEqual(config.ai.provider, "groq")
        self.assertEqual(config.ai.temperature, 0.3)

    def test_rejects_missing_section(self) -> None:
        with self.assertRaisesRegex(ConfigError, "'ai'"):
            load_config(self.write_config({"assistant": {}}))

    def test_rejects_out_of_range_temperature(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["ai"]["temperature"] = 3

        with self.assertRaisesRegex(ConfigError, "between 0 and 2"):
            load_config(self.write_config(data))

    def test_rejects_nonpositive_max_tokens(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["ai"]["max_completion_tokens"] = 0

        with self.assertRaisesRegex(ConfigError, "positive integer"):
            load_config(self.write_config(data))

    def test_loads_loopback_server_with_profile_client(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {"enabled": True, "database_path": "argus.db"}
        data["server"] = {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8765,
            "clients": [
                {
                    "id": "owner-laptop",
                    "profile_id": "owner",
                    "display_name": "Owner",
                    "role": "owner",
                    "token_env": "ARGUS_SERVER_TOKEN",
                }
            ],
        }

        config = load_config(self.write_config(data))

        self.assertTrue(config.server.enabled)
        self.assertEqual(config.server.host, "127.0.0.1")
        self.assertEqual(config.server.clients[0].profile_id, "owner")

    def test_enabled_server_requires_memory(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["server"] = {
            "enabled": True,
            "clients": [
                {
                    "id": "owner",
                    "profile_id": "owner",
                    "display_name": "Owner",
                    "role": "owner",
                    "token_env": "ARGUS_SERVER_TOKEN",
                }
            ],
        }

        with self.assertRaisesRegex(ConfigError, "requires 'memory.enabled'"):
            load_config(self.write_config(data))

    def test_nonloopback_server_requires_tls(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {"enabled": True, "database_path": "argus.db"}
        data["server"] = {
            "enabled": True,
            "host": "192.168.1.10",
            "clients": [
                {
                    "id": "owner",
                    "profile_id": "owner",
                    "display_name": "Owner",
                    "role": "owner",
                    "token_env": "ARGUS_SERVER_TOKEN",
                }
            ],
        }

        with self.assertRaisesRegex(ConfigError, "requires a TLS certificate"):
            load_config(self.write_config(data))

    def test_server_rejects_duplicate_token_environment(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {"enabled": True, "database_path": "argus.db"}
        shared = {
            "profile_id": "owner",
            "display_name": "Owner",
            "role": "owner",
            "token_env": "ARGUS_SERVER_TOKEN",
        }
        data["server"] = {
            "enabled": True,
            "clients": [
                {"id": "one", **shared},
                {"id": "two", **shared},
            ],
        }

        with self.assertRaisesRegex(ConfigError, "Duplicate server token"):
            load_config(self.write_config(data))

    def test_server_rejects_mutual_tls_ca_without_certificate(self) -> None:
        ca_file = self.write_config({"placeholder": True})
        data = json.loads(json.dumps(VALID_CONFIG))
        data["server"] = {"tls_ca_path": str(ca_file)}

        with self.assertRaisesRegex(ConfigError, "requires a TLS certificate"):
            load_config(self.write_config(data))

    def test_loads_dashboard_configuration_with_server(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {"enabled": True, "database_path": "argus.db"}
        data["server"] = {
            "enabled": True,
            "clients": [
                {
                    "id": "owner",
                    "profile_id": "owner",
                    "display_name": "Owner",
                    "role": "owner",
                    "token_env": "ARGUS_SERVER_TOKEN",
                }
            ],
        }
        data["dashboard"] = {
            "enabled": True,
            "refresh_interval_seconds": 45,
            "window_width": 1200,
            "window_height": 800,
            "notification_limit": 25,
        }

        config = load_config(self.write_config(data))

        self.assertTrue(config.dashboard.enabled)
        self.assertEqual(config.dashboard.refresh_interval_seconds, 45)
        self.assertEqual(config.dashboard.window_width, 1200)
        self.assertEqual(config.dashboard.notification_limit, 25)
        self.assertEqual(config.dashboard.idle_timeout_seconds, 120)

    def test_enabled_dashboard_is_standalone(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["dashboard"] = {"enabled": True}

        config = load_config(self.write_config(data))

        self.assertTrue(config.dashboard.enabled)
        self.assertFalse(config.server.enabled)
        self.assertEqual(config.dashboard.idle_timeout_seconds, 120)
        self.assertEqual(
            config.dashboard.wake_phrase, "wake up argus i am back"
        )

    def test_dashboard_rejects_too_fast_refresh(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["dashboard"] = {"refresh_interval_seconds": 1}

        with self.assertRaisesRegex(ConfigError, "between 5 and 3600"):
            load_config(self.write_config(data))

    def test_dashboard_rejects_idle_timeout_below_thirty_seconds(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["dashboard"] = {"idle_timeout_seconds": 29}

        with self.assertRaisesRegex(ConfigError, "between 30 and 3600"):
            load_config(self.write_config(data))

    def test_loads_and_resolves_enabled_tool_configuration(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["tools"] = {
            "enabled": True,
            "max_rounds": 4,
            "allowed_roots": ["."],
            "applications": {"notepad": "notepad.exe"},
            "web_applications": {"youtube": "https://www.youtube.com/"},
            "allowed_commands": ["whoami.exe"],
        }
        path = self.write_config(data)

        config = load_config(path)

        self.assertTrue(config.tools.enabled)
        self.assertEqual(config.tools.max_rounds, 4)
        self.assertEqual(config.tools.allowed_roots, (path.parent.resolve(),))
        self.assertEqual(config.tools.applications[0].alias, "notepad")
        self.assertEqual(config.tools.web_applications[0].alias, "youtube")
        self.assertEqual(
            config.tools.web_applications[0].url, "https://www.youtube.com/"
        )

    def test_enabled_tools_require_complete_allowlists(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["tools"] = {"enabled": True, "allowed_roots": ["."]}

        with self.assertRaisesRegex(ConfigError, "require at least one"):
            load_config(self.write_config(data))

    def test_command_allowlist_rejects_paths(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["tools"] = {
            "allowed_commands": [r"C:\\Windows\\System32\\cmd.exe"]
        }

        with self.assertRaisesRegex(ConfigError, "bare executable"):
            load_config(self.write_config(data))

    def test_web_application_rejects_local_or_non_https_url(self) -> None:
        for url in ("http://example.com/", "https://127.0.0.1/"):
            with self.subTest(url=url):
                data = json.loads(json.dumps(VALID_CONFIG))
                data["tools"] = {"web_applications": {"unsafe": url}}

                with self.assertRaisesRegex(ConfigError, "Web application 'unsafe'"):
                    load_config(self.write_config(data))

    def test_loads_voice_configuration(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["voice"] = {
            "enabled": True,
            "stt_provider": "groq",
            "stt_model": "whisper-large-v3-turbo",
            "language": "EN",
            "sample_rate": 16000,
            "max_recording_seconds": 15,
            "minimum_recording_seconds": 0.3,
            "tts_enabled": True,
            "tts_rate": 170,
            "tts_volume": 0.8,
            "preferred_voice_keywords": ["David", "male"],
        }

        config = load_config(self.write_config(data))

        self.assertTrue(config.voice.enabled)
        self.assertEqual(config.voice.language, "en")
        self.assertEqual(config.voice.max_recording_seconds, 15)
        self.assertEqual(config.voice.preferred_voice_keywords, ("david", "male"))

    def test_rejects_unbounded_voice_recording(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["voice"] = {"max_recording_seconds": 300}

        with self.assertRaisesRegex(ConfigError, "between 1 and 60"):
            load_config(self.write_config(data))

    def test_rejects_unknown_enabled_speech_provider(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["voice"] = {"enabled": True, "stt_provider": "unknown"}

        with self.assertRaisesRegex(ConfigError, "only Groq"):
            load_config(self.write_config(data))

    def test_loads_wake_configuration_and_adds_primary_phrase(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["voice"] = {"enabled": True}
        data["wake"] = {
            "enabled": True,
            "backend": "vosk",
            "phrase": "Argus",
            "recognition_aliases": ["August", "Argos"],
            "model_path": ".",
            "sample_rate": 16000,
            "command_max_seconds": 12,
            "silence_seconds": 1.0,
            "speech_threshold": 450,
            "command_attempts": 2,
            "acknowledgement": "Yes, sir?",
        }
        path = self.write_config(data)

        config = load_config(path)

        self.assertTrue(config.wake.enabled)
        self.assertEqual(config.wake.phrase, "argus")
        self.assertEqual(
            config.wake.recognition_aliases, ("argus", "august", "argos")
        )
        self.assertEqual(config.wake.model_path, path.parent.resolve())
        self.assertEqual(config.wake.command_attempts, 2)

    def test_wake_mode_requires_voice_mode(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["wake"] = {"enabled": True, "model_path": "."}

        with self.assertRaisesRegex(ConfigError, "requires 'voice.enabled'"):
            load_config(self.write_config(data))

    def test_rejects_unsafe_wake_recording_limit(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["wake"] = {"command_max_seconds": 120}

        with self.assertRaisesRegex(ConfigError, "between 3 and 30"):
            load_config(self.write_config(data))

    def test_loads_and_resolves_memory_configuration(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {
            "enabled": True,
            "database_path": "data/argus.db",
            "profile_id": "owner",
            "profile_name": "Owner",
            "conversation_context_messages": 24,
        }
        path = self.write_config(data)

        config = load_config(path)

        self.assertTrue(config.memory.enabled)
        self.assertEqual(
            config.memory.database_path, (path.parent / "data/argus.db").resolve()
        )
        self.assertEqual(config.memory.conversation_context_messages, 24)

    def test_enabled_memory_requires_database_path(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {"enabled": True}

        with self.assertRaisesRegex(ConfigError, "requires 'memory.database_path'"):
            load_config(self.write_config(data))

    def test_memory_path_expands_windows_environment_variables(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {
            "enabled": True,
            "database_path": "%LOCALAPPDATA%/Argus/argus.db",
        }
        path = self.write_config(data)
        local_data = path.parent / "local-data"

        with patch.dict("os.environ", {"LOCALAPPDATA": str(local_data)}):
            config = load_config(path)

        self.assertEqual(
            config.memory.database_path, (local_data / "Argus/argus.db").resolve()
        )

    def test_memory_context_limit_must_be_even(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {"conversation_context_messages": 3}

        with self.assertRaisesRegex(ConfigError, "even number"):
            load_config(self.write_config(data))

    def test_loads_and_resolves_vision_configuration(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["vision"] = {
            "enabled": True,
            "allowed_image_roots": ["."],
            "object_model_path": "models/object.tflite",
            "gesture_model_path": "models/gesture.task",
            "face_model_path": "models/face.tflite",
            "object_score_threshold": 0.4,
            "gesture_score_threshold": 0.7,
        }
        path = self.write_config(data)

        config = load_config(path)

        self.assertTrue(config.vision.enabled)
        self.assertEqual(config.vision.allowed_image_roots, (path.parent.resolve(),))
        self.assertEqual(
            config.vision.object_model_path,
            (path.parent / "models/object.tflite").resolve(),
        )

    def test_enabled_vision_requires_roots_and_models(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["vision"] = {"enabled": True}

        with self.assertRaisesRegex(ConfigError, "approved image root"):
            load_config(self.write_config(data))

    def test_rejects_unsafe_vision_threshold(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["vision"] = {"object_score_threshold": 0}

        with self.assertRaisesRegex(ConfigError, "between 0.1 and 1.0"):
            load_config(self.write_config(data))

    def test_loads_simulated_robotics_device(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["robotics"] = {
            "enabled": True,
            "devices": [
                {
                    "id": "sim_robot",
                    "name": "Simulator",
                    "transport": "simulator",
                    "actuators_enabled": True,
                    "allowed_actuators": ["led", "servo"],
                    "telemetry": {"distance_cm": "cm"},
                    "simulated_telemetry": {"distance_cm": 240},
                }
            ],
        }

        config = load_config(self.write_config(data))

        self.assertTrue(config.robotics.enabled)
        self.assertEqual(config.robotics.devices[0].device_id, "sim_robot")
        self.assertEqual(
            config.robotics.devices[0].simulated_telemetry,
            (("distance_cm", 240),),
        )

    def test_serial_robotics_device_requires_exact_port(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["robotics"] = {
            "enabled": True,
            "devices": [
                {
                    "id": "arduino",
                    "name": "Arduino",
                    "transport": "serial",
                }
            ],
        }

        with self.assertRaisesRegex(ConfigError, "requires a port"):
            load_config(self.write_config(data))

    def test_simulated_telemetry_must_be_allowlisted(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["robotics"] = {
            "enabled": True,
            "devices": [
                {
                    "id": "sim_robot",
                    "name": "Simulator",
                    "telemetry": {},
                    "simulated_telemetry": {"secret": "not allowlisted"},
                }
            ],
        }

        with self.assertRaisesRegex(ConfigError, "not allowlisted"):
            load_config(self.write_config(data))

    def test_loads_proactive_notification_configuration(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["memory"] = {
            "enabled": True,
            "database_path": "argus.db",
        }
        data["proactive"] = {
            "enabled": True,
            "poll_interval_seconds": 60,
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "06:30",
            "minimum_priority": "high",
            "enabled_categories": ["deadlines", "system"],
            "calendar_lead_minutes": 30,
            "max_notifications_per_cycle": 3,
            "battery_warning_percent": 20,
            "disk_free_warning_percent": 8,
        }

        config = load_config(self.write_config(data))

        self.assertTrue(config.proactive.enabled)
        self.assertEqual(config.proactive.minimum_priority, "high")
        self.assertEqual(
            config.proactive.enabled_categories, ("deadlines", "system")
        )

    def test_proactive_notifications_require_memory(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["proactive"] = {"enabled": True}

        with self.assertRaisesRegex(ConfigError, "require 'memory.enabled'"):
            load_config(self.write_config(data))

    def test_rejects_invalid_quiet_hours(self) -> None:
        data = json.loads(json.dumps(VALID_CONFIG))
        data["proactive"] = {"quiet_hours_start": "25:00"}

        with self.assertRaisesRegex(ConfigError, "HH:MM"):
            load_config(self.write_config(data))


if __name__ == "__main__":
    unittest.main()
