"""Human-readable JSON configuration with strict validation."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


class ConfigError(RuntimeError):
    """The Argus configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class AssistantConfig:
    name: str
    purpose: str


@dataclass(frozen=True, slots=True)
class AIConfig:
    provider: str
    model: str
    temperature: float
    max_completion_tokens: int


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    alias: str
    executable: str


@dataclass(frozen=True, slots=True)
class WebApplicationConfig:
    alias: str
    url: str


@dataclass(frozen=True, slots=True)
class ToolsConfig:
    enabled: bool = False
    max_rounds: int = 6
    allowed_roots: tuple[Path, ...] = ()
    applications: tuple[ApplicationConfig, ...] = ()
    web_applications: tuple[WebApplicationConfig, ...] = ()
    allowed_commands: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    enabled: bool = False
    stt_provider: str = "groq"
    stt_model: str = "whisper-large-v3-turbo"
    language: str | None = "en"
    sample_rate: int = 16_000
    max_recording_seconds: int = 20
    minimum_recording_seconds: float = 0.4
    tts_enabled: bool = True
    tts_rate: int = 175
    tts_volume: float = 1.0
    preferred_voice_keywords: tuple[str, ...] = ("david", "mark", "male")


@dataclass(frozen=True, slots=True)
class WakeConfig:
    enabled: bool = False
    backend: str = "vosk"
    phrase: str = "argus"
    recognition_aliases: tuple[str, ...] = ("argus", "august", "argos")
    model_path: Path | None = None
    sample_rate: int = 16_000
    command_max_seconds: int = 15
    silence_seconds: float = 1.2
    speech_threshold: int = 500
    command_attempts: int = 2
    acknowledgement: str = "Yes, sir?"


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    enabled: bool = False
    database_path: Path | None = None
    profile_id: str = "owner"
    profile_name: str = "Owner"
    conversation_context_messages: int = 20


@dataclass(frozen=True, slots=True)
class VisionConfig:
    enabled: bool = False
    allowed_image_roots: tuple[Path, ...] = ()
    camera_index: int = 0
    capture_width: int = 1280
    capture_height: int = 720
    warmup_frames: int = 5
    object_model_path: Path | None = None
    gesture_model_path: Path | None = None
    face_model_path: Path | None = None
    object_score_threshold: float = 0.45
    gesture_score_threshold: float = 0.60
    max_results: int = 10
    detect_faces: bool = True


@dataclass(frozen=True, slots=True)
class TelemetryChannelConfig:
    name: str
    unit: str


@dataclass(frozen=True, slots=True)
class RoboticsDeviceConfig:
    device_id: str
    name: str
    transport: str = "simulator"
    port: str | None = None
    baud_rate: int = 115_200
    timeout_seconds: float = 2.0
    startup_delay_seconds: float = 2.0
    actuators_enabled: bool = False
    allowed_actuators: tuple[str, ...] = ()
    telemetry_channels: tuple[TelemetryChannelConfig, ...] = ()
    simulated_telemetry: tuple[tuple[str, str | int | float | bool], ...] = ()


@dataclass(frozen=True, slots=True)
class RoboticsConfig:
    enabled: bool = False
    devices: tuple[RoboticsDeviceConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class ProactiveConfig:
    enabled: bool = False
    poll_interval_seconds: int = 30
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    minimum_priority: str = "normal"
    enabled_categories: tuple[str, ...] = (
        "reminders",
        "deadlines",
        "calendar",
        "system",
    )
    calendar_lead_minutes: int = 15
    max_notifications_per_cycle: int = 5
    battery_warning_percent: int = 15
    disk_free_warning_percent: int = 10


@dataclass(frozen=True, slots=True)
class ServerClientConfig:
    client_id: str
    profile_id: str
    display_name: str
    role: str
    token_env: str


@dataclass(frozen=True, slots=True)
class ServerConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    max_request_bytes: int = 16_384
    requests_per_minute: int = 30
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None
    tls_ca_path: Path | None = None
    clients: tuple[ServerClientConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    enabled: bool = False
    refresh_interval_seconds: int = 30
    window_width: int = 1100
    window_height: int = 720
    notification_limit: int = 50
    idle_timeout_seconds: int = 120
    wake_phrase: str = "wake up argus i am back"


@dataclass(frozen=True, slots=True)
class AppConfig:
    assistant: AssistantConfig
    ai: AIConfig
    source: Path
    tools: ToolsConfig = ToolsConfig()
    voice: VoiceConfig = VoiceConfig()
    wake: WakeConfig = WakeConfig()
    memory: MemoryConfig = MemoryConfig()
    vision: VisionConfig = VisionConfig()
    robotics: RoboticsConfig = RoboticsConfig()
    proactive: ProactiveConfig = ProactiveConfig()
    server: ServerConfig = ServerConfig()
    dashboard: DashboardConfig = DashboardConfig()


def default_config_path() -> Path:
    configured_path = os.environ.get("ARGUS_CONFIG")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "config" / "argus.json"


def _required_text(data: dict[str, Any], key: str, section: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{section}.{key}' must be a non-empty string.")
    return value.strip()


def _validate_configured_public_url(url: str, alias: str) -> str:
    normalized = url.strip()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"Web application '{alias}' has an invalid URL.") from exc

    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ConfigError(
            f"Web application '{alias}' must use a normal public HTTPS URL."
        )

    host = hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        raise ConfigError(f"Web application '{alias}' cannot target localhost.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ConfigError(
            f"Web application '{alias}' cannot target a private or local address."
        )
    return normalized


def _load_tools(raw: object, config_path: Path) -> ToolsConfig:
    if raw is None:
        return ToolsConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'tools' must be a JSON object.")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("'tools.enabled' must be true or false.")

    max_rounds = raw.get("max_rounds", 6)
    if (
        not isinstance(max_rounds, int)
        or isinstance(max_rounds, bool)
        or not 1 <= max_rounds <= 12
    ):
        raise ConfigError("'tools.max_rounds' must be an integer between 1 and 12.")

    roots_raw = raw.get("allowed_roots", [])
    if not isinstance(roots_raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in roots_raw
    ):
        raise ConfigError("'tools.allowed_roots' must be a list of folder paths.")
    roots: list[Path] = []
    for item in roots_raw:
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        resolved = candidate.resolve()
        if not resolved.is_dir():
            raise ConfigError(f"Approved search folder does not exist: {resolved}")
        if resolved not in roots:
            roots.append(resolved)

    applications_raw = raw.get("applications", {})
    if not isinstance(applications_raw, dict):
        raise ConfigError("'tools.applications' must be an object of approved apps.")
    applications: list[ApplicationConfig] = []
    for alias, executable in applications_raw.items():
        if not isinstance(alias, str) or not re.fullmatch(r"[a-z0-9_-]+", alias):
            raise ConfigError(
                "Application aliases may contain lowercase letters, numbers, '_' or '-'."
            )
        if not isinstance(executable, str) or not executable.strip() or "\0" in executable:
            raise ConfigError(f"Application '{alias}' needs one executable path or name.")
        applications.append(ApplicationConfig(alias, executable.strip()))

    web_applications_raw = raw.get("web_applications", {})
    if not isinstance(web_applications_raw, dict):
        raise ConfigError(
            "'tools.web_applications' must be an object of approved web apps."
        )
    web_applications: list[WebApplicationConfig] = []
    for alias, url in web_applications_raw.items():
        if not isinstance(alias, str) or not re.fullmatch(r"[a-z0-9_-]+", alias):
            raise ConfigError(
                "Web application aliases may contain lowercase letters, numbers, "
                "'_' or '-'."
            )
        if not isinstance(url, str) or not url.strip() or "\0" in url:
            raise ConfigError(f"Web application '{alias}' needs one HTTPS URL.")
        web_applications.append(
            WebApplicationConfig(alias, _validate_configured_public_url(url, alias))
        )

    commands_raw = raw.get("allowed_commands", [])
    if not isinstance(commands_raw, list) or any(
        not isinstance(item, str)
        or not item.strip()
        or Path(item).name != item
        or any(character.isspace() for character in item)
        for item in commands_raw
    ):
        raise ConfigError(
            "'tools.allowed_commands' must contain only bare executable names."
        )
    commands = tuple(dict.fromkeys(item.strip() for item in commands_raw))

    if enabled and (not roots or not applications or not commands):
        raise ConfigError(
            "Enabled tools require at least one allowed root, application, and command."
        )

    return ToolsConfig(
        enabled=enabled,
        max_rounds=max_rounds,
        allowed_roots=tuple(roots),
        applications=tuple(applications),
        web_applications=tuple(web_applications),
        allowed_commands=commands,
    )


def _load_voice(raw: object) -> VoiceConfig:
    if raw is None:
        return VoiceConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'voice' must be a JSON object.")

    enabled = raw.get("enabled", False)
    tts_enabled = raw.get("tts_enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("'voice.enabled' must be true or false.")
    if not isinstance(tts_enabled, bool):
        raise ConfigError("'voice.tts_enabled' must be true or false.")

    stt_provider = raw.get("stt_provider", "groq")
    stt_model = raw.get("stt_model", "whisper-large-v3-turbo")
    if not isinstance(stt_provider, str) or not stt_provider.strip():
        raise ConfigError("'voice.stt_provider' must be a non-empty string.")
    if not isinstance(stt_model, str) or not stt_model.strip():
        raise ConfigError("'voice.stt_model' must be a non-empty string.")
    if enabled and stt_provider.casefold() != "groq":
        raise ConfigError("Tier 3 currently supports only Groq speech-to-text.")

    language = raw.get("language", "en")
    if language is not None and (
        not isinstance(language, str)
        or not re.fullmatch(r"[a-zA-Z]{2}", language.strip())
    ):
        raise ConfigError("'voice.language' must be a two-letter code or null.")
    normalized_language = language.strip().casefold() if language else None

    sample_rate = raw.get("sample_rate", 16_000)
    if (
        not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or not 8_000 <= sample_rate <= 48_000
    ):
        raise ConfigError("'voice.sample_rate' must be between 8000 and 48000.")

    maximum = raw.get("max_recording_seconds", 20)
    if (
        not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or not 1 <= maximum <= 60
    ):
        raise ConfigError(
            "'voice.max_recording_seconds' must be an integer between 1 and 60."
        )

    minimum = raw.get("minimum_recording_seconds", 0.4)
    if (
        not isinstance(minimum, (int, float))
        or isinstance(minimum, bool)
        or not 0.1 <= float(minimum) <= 2.0
        or float(minimum) >= maximum
    ):
        raise ConfigError(
            "'voice.minimum_recording_seconds' must be between 0.1 and 2.0 "
            "and shorter than the maximum."
        )

    rate = raw.get("tts_rate", 175)
    if not isinstance(rate, int) or isinstance(rate, bool) or not 80 <= rate <= 300:
        raise ConfigError("'voice.tts_rate' must be an integer between 80 and 300.")

    volume = raw.get("tts_volume", 1.0)
    if not isinstance(volume, (int, float)) or isinstance(volume, bool):
        raise ConfigError("'voice.tts_volume' must be a number between 0 and 1.")
    volume = float(volume)
    if not 0 <= volume <= 1:
        raise ConfigError("'voice.tts_volume' must be between 0 and 1.")

    keywords_raw = raw.get(
        "preferred_voice_keywords", ["david", "mark", "male"]
    )
    if not isinstance(keywords_raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in keywords_raw
    ):
        raise ConfigError(
            "'voice.preferred_voice_keywords' must be a list of words."
        )
    keywords = tuple(dict.fromkeys(item.strip().casefold() for item in keywords_raw))

    return VoiceConfig(
        enabled=enabled,
        stt_provider=stt_provider.strip().casefold(),
        stt_model=stt_model.strip(),
        language=normalized_language,
        sample_rate=sample_rate,
        max_recording_seconds=maximum,
        minimum_recording_seconds=float(minimum),
        tts_enabled=tts_enabled,
        tts_rate=rate,
        tts_volume=volume,
        preferred_voice_keywords=keywords,
    )


def _load_wake(raw: object, config_path: Path) -> WakeConfig:
    if raw is None:
        return WakeConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'wake' must be a JSON object.")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("'wake.enabled' must be true or false.")

    backend = raw.get("backend", "vosk")
    if not isinstance(backend, str) or not backend.strip():
        raise ConfigError("'wake.backend' must be a non-empty string.")
    backend = backend.strip().casefold()
    if enabled and backend != "vosk":
        raise ConfigError("Tier 4 currently supports only the Vosk wake backend.")

    phrase = raw.get("phrase", "argus")
    if not isinstance(phrase, str) or not phrase.strip():
        raise ConfigError("'wake.phrase' must be a non-empty string.")
    phrase = " ".join(phrase.strip().casefold().split())

    aliases_raw = raw.get("recognition_aliases", [phrase])
    if not isinstance(aliases_raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in aliases_raw
    ):
        raise ConfigError("'wake.recognition_aliases' must be a list of phrases.")
    aliases = tuple(
        dict.fromkeys(" ".join(item.strip().casefold().split()) for item in aliases_raw)
    )
    if phrase not in aliases:
        aliases = (phrase, *aliases)

    model_path_raw = raw.get("model_path")
    model_path = None
    if model_path_raw is not None:
        if not isinstance(model_path_raw, str) or not model_path_raw.strip():
            raise ConfigError("'wake.model_path' must be a folder path.")
        candidate = Path(model_path_raw).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        model_path = candidate.resolve()
    if enabled and model_path is None:
        raise ConfigError("Enabled wake mode requires 'wake.model_path'.")

    sample_rate = raw.get("sample_rate", 16_000)
    if (
        not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or not 8_000 <= sample_rate <= 48_000
    ):
        raise ConfigError("'wake.sample_rate' must be between 8000 and 48000.")

    maximum = raw.get("command_max_seconds", 15)
    if (
        not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or not 3 <= maximum <= 30
    ):
        raise ConfigError(
            "'wake.command_max_seconds' must be an integer between 3 and 30."
        )

    silence = raw.get("silence_seconds", 1.2)
    if (
        not isinstance(silence, (int, float))
        or isinstance(silence, bool)
        or not 0.5 <= float(silence) <= 3.0
    ):
        raise ConfigError("'wake.silence_seconds' must be between 0.5 and 3.0.")

    threshold = raw.get("speech_threshold", 500)
    if (
        not isinstance(threshold, int)
        or isinstance(threshold, bool)
        or not 50 <= threshold <= 10_000
    ):
        raise ConfigError("'wake.speech_threshold' must be between 50 and 10000.")

    attempts = raw.get("command_attempts", 2)
    if (
        not isinstance(attempts, int)
        or isinstance(attempts, bool)
        or not 1 <= attempts <= 3
    ):
        raise ConfigError("'wake.command_attempts' must be between 1 and 3.")

    acknowledgement = raw.get("acknowledgement", "Yes, sir?")
    if (
        not isinstance(acknowledgement, str)
        or not acknowledgement.strip()
        or len(acknowledgement) > 100
    ):
        raise ConfigError("'wake.acknowledgement' must contain 1 to 100 characters.")

    return WakeConfig(
        enabled=enabled,
        backend=backend,
        phrase=phrase,
        recognition_aliases=aliases,
        model_path=model_path,
        sample_rate=sample_rate,
        command_max_seconds=maximum,
        silence_seconds=float(silence),
        speech_threshold=threshold,
        command_attempts=attempts,
        acknowledgement=acknowledgement.strip(),
    )


def _load_memory(raw: object, config_path: Path) -> MemoryConfig:
    if raw is None:
        return MemoryConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'memory' must be a JSON object.")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("'memory.enabled' must be true or false.")

    database_path_raw = raw.get("database_path")
    database_path = None
    if database_path_raw is not None:
        if not isinstance(database_path_raw, str) or not database_path_raw.strip():
            raise ConfigError("'memory.database_path' must be a file path.")
        candidate = Path(os.path.expandvars(database_path_raw)).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        database_path = candidate.resolve()
    if enabled and database_path is None:
        raise ConfigError("Enabled memory requires 'memory.database_path'.")

    profile_id = raw.get("profile_id", "owner")
    if not isinstance(profile_id, str) or not re.fullmatch(
        r"[a-z0-9_-]{1,50}", profile_id
    ):
        raise ConfigError(
            "'memory.profile_id' may contain lowercase letters, numbers, '_' or '-'."
        )

    profile_name = raw.get("profile_name", "Owner")
    if (
        not isinstance(profile_name, str)
        or not profile_name.strip()
        or len(profile_name.strip()) > 100
    ):
        raise ConfigError("'memory.profile_name' must contain 1 to 100 characters.")

    context_messages = raw.get("conversation_context_messages", 20)
    if (
        not isinstance(context_messages, int)
        or isinstance(context_messages, bool)
        or not 2 <= context_messages <= 100
        or context_messages % 2 != 0
    ):
        raise ConfigError(
            "'memory.conversation_context_messages' must be an even number "
            "between 2 and 100."
        )

    return MemoryConfig(
        enabled=enabled,
        database_path=database_path,
        profile_id=profile_id,
        profile_name=profile_name.strip(),
        conversation_context_messages=context_messages,
    )


def _load_vision(raw: object, config_path: Path) -> VisionConfig:
    if raw is None:
        return VisionConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'vision' must be a JSON object.")

    enabled = raw.get("enabled", False)
    detect_faces = raw.get("detect_faces", True)
    if not isinstance(enabled, bool):
        raise ConfigError("'vision.enabled' must be true or false.")
    if not isinstance(detect_faces, bool):
        raise ConfigError("'vision.detect_faces' must be true or false.")

    def bounded_integer(key: str, default: int, minimum: int, maximum: int) -> int:
        value = raw.get(key, default)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise ConfigError(
                f"'vision.{key}' must be between {minimum} and {maximum}."
            )
        return value

    def threshold(key: str, default: float) -> float:
        value = raw.get(key, default)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0.1 <= float(value) <= 1.0
        ):
            raise ConfigError(f"'vision.{key}' must be between 0.1 and 1.0.")
        return float(value)

    def model_path(key: str) -> Path | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"'vision.{key}' must be a file path.")
        candidate = Path(os.path.expandvars(value)).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        return candidate.resolve()

    object_model_path = model_path("object_model_path")
    gesture_model_path = model_path("gesture_model_path")
    face_model_path = model_path("face_model_path")
    roots_raw = raw.get("allowed_image_roots", [])
    if not isinstance(roots_raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in roots_raw
    ):
        raise ConfigError("'vision.allowed_image_roots' must be a list of folders.")
    roots: list[Path] = []
    for item in roots_raw:
        candidate = Path(os.path.expandvars(item)).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        resolved = candidate.resolve()
        if not resolved.is_dir():
            raise ConfigError(f"Approved vision folder does not exist: {resolved}")
        if resolved not in roots:
            roots.append(resolved)

    if enabled and (
        object_model_path is None
        or gesture_model_path is None
        or (detect_faces and face_model_path is None)
        or not roots
    ):
        raise ConfigError(
            "Enabled vision requires an approved image root and its model paths."
        )

    return VisionConfig(
        enabled=enabled,
        allowed_image_roots=tuple(roots),
        camera_index=bounded_integer("camera_index", 0, 0, 10),
        capture_width=bounded_integer("capture_width", 1280, 320, 3840),
        capture_height=bounded_integer("capture_height", 720, 240, 2160),
        warmup_frames=bounded_integer("warmup_frames", 5, 1, 30),
        object_model_path=object_model_path,
        gesture_model_path=gesture_model_path,
        face_model_path=face_model_path,
        object_score_threshold=threshold("object_score_threshold", 0.45),
        gesture_score_threshold=threshold("gesture_score_threshold", 0.60),
        max_results=bounded_integer("max_results", 10, 1, 50),
        detect_faces=detect_faces,
    )


def _load_robotics(raw: object) -> RoboticsConfig:
    if raw is None:
        return RoboticsConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'robotics' must be a JSON object.")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("'robotics.enabled' must be true or false.")
    devices_raw = raw.get("devices", [])
    if not isinstance(devices_raw, list):
        raise ConfigError("'robotics.devices' must be a list.")

    known_actuators = {"led", "servo", "motor_left", "motor_right"}
    devices: list[RoboticsDeviceConfig] = []
    device_ids: set[str] = set()
    for index, item in enumerate(devices_raw):
        section = f"robotics.devices[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"'{section}' must be a JSON object.")

        device_id = item.get("id")
        if not isinstance(device_id, str) or not re.fullmatch(
            r"[a-z0-9_-]{1,50}", device_id
        ):
            raise ConfigError(
                f"'{section}.id' may contain lowercase letters, numbers, '_' or '-'."
            )
        if device_id in device_ids:
            raise ConfigError(f"Duplicate robotics device id: {device_id}")
        device_ids.add(device_id)

        name = item.get("name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 100
        ):
            raise ConfigError(f"'{section}.name' must contain 1 to 100 characters.")

        transport = item.get("transport", "simulator")
        if not isinstance(transport, str) or transport.casefold() not in {
            "simulator",
            "serial",
        }:
            raise ConfigError(
                f"'{section}.transport' must be 'simulator' or 'serial'."
            )
        transport = transport.casefold()

        port = item.get("port")
        if port is not None and (
            not isinstance(port, str)
            or not port.strip()
            or len(port.strip()) > 200
            or any(character.isspace() or ord(character) < 32 for character in port)
            or "://" in port
        ):
            raise ConfigError(
                f"'{section}.port' must be one exact local serial port name."
            )
        port = port.strip() if isinstance(port, str) else None
        if transport == "serial" and port is None:
            raise ConfigError(f"Serial device '{device_id}' requires a port.")
        if transport == "simulator" and port is not None:
            raise ConfigError(f"Simulator device '{device_id}' cannot define a port.")

        baud_rate = item.get("baud_rate", 115_200)
        if (
            not isinstance(baud_rate, int)
            or isinstance(baud_rate, bool)
            or not 1_200 <= baud_rate <= 2_000_000
        ):
            raise ConfigError(f"'{section}.baud_rate' must be between 1200 and 2000000.")

        def bounded_number(key: str, default: float, minimum: float, maximum: float) -> float:
            value = item.get(key, default)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not minimum <= float(value) <= maximum
            ):
                raise ConfigError(
                    f"'{section}.{key}' must be between {minimum} and {maximum}."
                )
            return float(value)

        actuators_enabled = item.get("actuators_enabled", False)
        if not isinstance(actuators_enabled, bool):
            raise ConfigError(f"'{section}.actuators_enabled' must be true or false.")
        actuators_raw = item.get("allowed_actuators", [])
        if not isinstance(actuators_raw, list) or any(
            not isinstance(value, str) or value not in known_actuators
            for value in actuators_raw
        ):
            raise ConfigError(
                f"'{section}.allowed_actuators' may contain only: "
                + ", ".join(sorted(known_actuators))
                + "."
            )
        actuators = tuple(dict.fromkeys(actuators_raw))
        if actuators_enabled and not actuators:
            raise ConfigError(
                f"Device '{device_id}' enables actuators but has no actuator allowlist."
            )

        telemetry_raw = item.get("telemetry", {})
        if not isinstance(telemetry_raw, dict):
            raise ConfigError(f"'{section}.telemetry' must map channel names to units.")
        channels: list[TelemetryChannelConfig] = []
        for channel, unit in telemetry_raw.items():
            if not isinstance(channel, str) or not re.fullmatch(
                r"[a-z][a-z0-9_]{0,39}", channel
            ):
                raise ConfigError(f"Invalid telemetry channel in '{section}.telemetry'.")
            if (
                not isinstance(unit, str)
                or len(unit) > 20
                or any(ord(character) < 32 for character in unit)
            ):
                raise ConfigError(
                    f"Telemetry unit for '{channel}' must contain at most 20 characters."
                )
            channels.append(TelemetryChannelConfig(channel, unit))

        simulated_raw = item.get("simulated_telemetry", {})
        if not isinstance(simulated_raw, dict):
            raise ConfigError(f"'{section}.simulated_telemetry' must be an object.")
        known_channels = {channel.name for channel in channels}
        simulated: list[tuple[str, str | int | float | bool]] = []
        for channel, value in simulated_raw.items():
            if channel not in known_channels:
                raise ConfigError(
                    f"Simulated telemetry channel '{channel}' is not allowlisted."
                )
            if (
                not isinstance(value, (str, int, float, bool))
                or isinstance(value, str) and len(value) > 100
                or isinstance(value, float) and not math.isfinite(value)
            ):
                raise ConfigError(
                    f"Simulated telemetry value for '{channel}' is invalid or oversized."
                )
            simulated.append((channel, value))
        if transport == "serial" and simulated:
            raise ConfigError(
                f"Serial device '{device_id}' cannot define simulated telemetry."
            )

        devices.append(
            RoboticsDeviceConfig(
                device_id=device_id,
                name=name.strip(),
                transport=transport,
                port=port,
                baud_rate=baud_rate,
                timeout_seconds=bounded_number("timeout_seconds", 2.0, 0.1, 10.0),
                startup_delay_seconds=bounded_number(
                    "startup_delay_seconds", 2.0, 0.0, 5.0
                ),
                actuators_enabled=actuators_enabled,
                allowed_actuators=actuators,
                telemetry_channels=tuple(channels),
                simulated_telemetry=tuple(simulated),
            )
        )

    if enabled and not devices:
        raise ConfigError("Enabled robotics requires at least one configured device.")
    return RoboticsConfig(enabled=enabled, devices=tuple(devices))


def _load_proactive(raw: object) -> ProactiveConfig:
    if raw is None:
        return ProactiveConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'proactive' must be a JSON object.")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("'proactive.enabled' must be true or false.")

    def bounded_integer(
        key: str, default: int, minimum: int, maximum: int
    ) -> int:
        value = raw.get(key, default)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise ConfigError(
                f"'proactive.{key}' must be between {minimum} and {maximum}."
            )
        return value

    def clock_time(key: str, default: str) -> str:
        value = raw.get(key, default)
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", value
        ):
            raise ConfigError(f"'proactive.{key}' must use 24-hour HH:MM.")
        return value

    priorities = {"low", "normal", "high", "critical"}
    minimum_priority = raw.get("minimum_priority", "normal")
    if not isinstance(minimum_priority, str) or minimum_priority not in priorities:
        raise ConfigError(
            "'proactive.minimum_priority' must be low, normal, high, or critical."
        )

    known_categories = {"reminders", "deadlines", "calendar", "system"}
    categories_raw = raw.get("enabled_categories", sorted(known_categories))
    if not isinstance(categories_raw, list) or any(
        not isinstance(category, str) or category not in known_categories
        for category in categories_raw
    ):
        raise ConfigError(
            "'proactive.enabled_categories' may contain reminders, deadlines, "
            "calendar, and system."
        )
    categories = tuple(dict.fromkeys(categories_raw))
    if enabled and not categories:
        raise ConfigError("Enabled proactive notifications require one category.")

    return ProactiveConfig(
        enabled=enabled,
        poll_interval_seconds=bounded_integer(
            "poll_interval_seconds", 30, 5, 3600
        ),
        quiet_hours_start=clock_time("quiet_hours_start", "22:00"),
        quiet_hours_end=clock_time("quiet_hours_end", "07:00"),
        minimum_priority=minimum_priority,
        enabled_categories=categories,
        calendar_lead_minutes=bounded_integer(
            "calendar_lead_minutes", 15, 0, 1440
        ),
        max_notifications_per_cycle=bounded_integer(
            "max_notifications_per_cycle", 5, 1, 20
        ),
        battery_warning_percent=bounded_integer(
            "battery_warning_percent", 15, 1, 100
        ),
        disk_free_warning_percent=bounded_integer(
            "disk_free_warning_percent", 10, 1, 50
        ),
    )


def _load_server(raw: object, config_path: Path) -> ServerConfig:
    if raw is None:
        return ServerConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'server' must be a JSON object.")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("'server.enabled' must be true or false.")
    host = raw.get("host", "127.0.0.1")
    if (
        not isinstance(host, str)
        or not host.strip()
        or len(host.strip()) > 255
        or any(character.isspace() or ord(character) < 32 for character in host)
        or "://" in host
        or "/" in host
        or "\\" in host
    ):
        raise ConfigError("'server.host' must be one host name or IP address.")
    host = host.strip()

    port = raw.get("port", 8765)
    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65_535
    ):
        raise ConfigError("'server.port' must be between 1 and 65535.")

    def bounded_integer(
        key: str, default: int, minimum: int, maximum: int
    ) -> int:
        value = raw.get(key, default)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise ConfigError(
                f"'server.{key}' must be between {minimum} and {maximum}."
            )
        return value

    def optional_file(key: str) -> Path | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"'server.{key}' must be a file path or null.")
        candidate = Path(os.path.expandvars(value)).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise ConfigError(f"Server TLS file does not exist: {resolved}")
        return resolved

    cert_path = optional_file("tls_cert_path")
    key_path = optional_file("tls_key_path")
    ca_path = optional_file("tls_ca_path")
    if (cert_path is None) != (key_path is None):
        raise ConfigError(
            "'server.tls_cert_path' and 'server.tls_key_path' must be set together."
        )
    if ca_path is not None and cert_path is None:
        raise ConfigError(
            "'server.tls_ca_path' requires a TLS certificate and key."
        )

    is_loopback = host.casefold() == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    if enabled and not is_loopback and (cert_path is None or key_path is None):
        raise ConfigError(
            "A non-loopback server host requires a TLS certificate and key."
        )

    clients_raw = raw.get("clients", [])
    if not isinstance(clients_raw, list) or len(clients_raw) > 20:
        raise ConfigError("'server.clients' must be a list of at most 20 clients.")
    clients: list[ServerClientConfig] = []
    client_ids: set[str] = set()
    token_envs: set[str] = set()
    for index, item in enumerate(clients_raw):
        section = f"server.clients[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"'{section}' must be a JSON object.")
        client_id = item.get("id")
        profile_id = item.get("profile_id")
        if not isinstance(client_id, str) or not re.fullmatch(
            r"[a-z0-9_-]{1,50}", client_id
        ):
            raise ConfigError(f"'{section}.id' is not a valid client id.")
        if client_id in client_ids:
            raise ConfigError(f"Duplicate server client id: {client_id}")
        client_ids.add(client_id)
        if not isinstance(profile_id, str) or not re.fullmatch(
            r"[a-z0-9_-]{1,50}", profile_id
        ):
            raise ConfigError(f"'{section}.profile_id' is not a valid profile id.")
        display_name = item.get("display_name")
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > 100
        ):
            raise ConfigError(
                f"'{section}.display_name' must contain 1 to 100 characters."
            )
        role = item.get("role", "user")
        if role not in {"owner", "user", "read_only"}:
            raise ConfigError(f"'{section}.role' must be owner, user, or read_only.")
        token_env = item.get("token_env")
        if not isinstance(token_env, str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{2,99}", token_env
        ):
            raise ConfigError(
                f"'{section}.token_env' must be an uppercase environment variable."
            )
        if token_env in token_envs:
            raise ConfigError(f"Duplicate server token environment: {token_env}")
        token_envs.add(token_env)
        clients.append(
            ServerClientConfig(
                client_id=client_id,
                profile_id=profile_id,
                display_name=display_name.strip(),
                role=role,
                token_env=token_env,
            )
        )
    if enabled and not clients:
        raise ConfigError("Enabled server mode requires at least one client.")

    return ServerConfig(
        enabled=enabled,
        host=host,
        port=port,
        max_request_bytes=bounded_integer(
            "max_request_bytes", 16_384, 1_024, 1_048_576
        ),
        requests_per_minute=bounded_integer(
            "requests_per_minute", 30, 1, 600
        ),
        tls_cert_path=cert_path,
        tls_key_path=key_path,
        tls_ca_path=ca_path,
        clients=tuple(clients),
    )


def _load_dashboard(raw: object) -> DashboardConfig:
    if raw is None:
        return DashboardConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'dashboard' must be a JSON object.")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("'dashboard.enabled' must be true or false.")

    def bounded_integer(
        key: str, default: int, minimum: int, maximum: int
    ) -> int:
        value = raw.get(key, default)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise ConfigError(
                f"'dashboard.{key}' must be between {minimum} and {maximum}."
            )
        return value

    wake_phrase = raw.get("wake_phrase", "wake up argus i am back")
    if (
        not isinstance(wake_phrase, str)
        or not wake_phrase.strip()
        or len(wake_phrase.strip()) > 100
    ):
        raise ConfigError("'dashboard.wake_phrase' must contain 1 to 100 characters.")

    return DashboardConfig(
        enabled=enabled,
        refresh_interval_seconds=bounded_integer(
            "refresh_interval_seconds", 30, 5, 3_600
        ),
        window_width=bounded_integer("window_width", 1_100, 800, 2_560),
        window_height=bounded_integer("window_height", 720, 600, 1_440),
        notification_limit=bounded_integer("notification_limit", 50, 1, 100),
        idle_timeout_seconds=bounded_integer(
            "idle_timeout_seconds", 120, 30, 3_600
        ),
        wake_phrase=" ".join(wake_phrase.casefold().split()),
    )


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path).expanduser().resolve() if path else default_config_path()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"Configuration file not found: {config_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read configuration '{config_path}': {error}") from error

    if not isinstance(raw, dict):
        raise ConfigError("The configuration root must be a JSON object.")
    assistant_raw = raw.get("assistant")
    ai_raw = raw.get("ai")
    if not isinstance(assistant_raw, dict):
        raise ConfigError("'assistant' must be a JSON object.")
    if not isinstance(ai_raw, dict):
        raise ConfigError("'ai' must be a JSON object.")

    voice_config = _load_voice(raw.get("voice"))
    wake_config = _load_wake(raw.get("wake"), config_path)
    memory_config = _load_memory(raw.get("memory"), config_path)
    proactive_config = _load_proactive(raw.get("proactive"))
    server_config = _load_server(raw.get("server"), config_path)
    dashboard_config = _load_dashboard(raw.get("dashboard"))
    if wake_config.enabled and not voice_config.enabled:
        raise ConfigError("Wake mode requires 'voice.enabled' to be true.")
    if proactive_config.enabled and not memory_config.enabled:
        raise ConfigError("Proactive notifications require 'memory.enabled' to be true.")
    if server_config.enabled and not memory_config.enabled:
        raise ConfigError("Server mode requires 'memory.enabled' to be true.")

    temperature = ai_raw.get("temperature")
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
        raise ConfigError("'ai.temperature' must be a number between 0 and 2.")
    temperature = float(temperature)
    if not 0 <= temperature <= 2:
        raise ConfigError("'ai.temperature' must be between 0 and 2.")

    max_tokens = ai_raw.get("max_completion_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ConfigError("'ai.max_completion_tokens' must be a positive integer.")

    return AppConfig(
        assistant=AssistantConfig(
            name=_required_text(assistant_raw, "name", "assistant"),
            purpose=_required_text(assistant_raw, "purpose", "assistant"),
        ),
        ai=AIConfig(
            provider=_required_text(ai_raw, "provider", "ai"),
            model=_required_text(ai_raw, "model", "ai"),
            temperature=temperature,
            max_completion_tokens=max_tokens,
        ),
        source=config_path,
        tools=_load_tools(raw.get("tools"), config_path),
        voice=voice_config,
        wake=wake_config,
        memory=memory_config,
        vision=_load_vision(raw.get("vision"), config_path),
        robotics=_load_robotics(raw.get("robotics")),
        proactive=proactive_config,
        server=server_config,
        dashboard=dashboard_config,
    )
