"""Local, on-demand runtime for the Argus Control Center."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import socket
from threading import Event
import time
from typing import Any

from argus.ai.factory import create_provider, resolve_api_key
from argus.config import AppConfig, VisionConfig
from argus.core import Agent
from argus.memory import (
    LocalMemoryStore,
    MemoryStoreError,
    build_memory_tool_definitions,
)
from argus.prompts import build_system_prompt
from argus.robotics import (
    RoboticsError,
    RoboticsService,
    build_robotics_tool_definitions,
)
from argus.tools import ToolDefinition, ToolRuntime, build_computer_tool_definitions
from argus.tools.runtime import Confirmer
from argus.vision import (
    LocalVisionService,
    build_vision_tool_definitions,
)
from argus.voice import VoiceError, VoiceSession
from argus.voice.factory import create_silence_stopping_voice_session
from argus.wake import WakeError
from argus.wake.vosk_detector import VoskWakeDetector


class DashboardError(RuntimeError):
    """The local Control Center could not initialize or complete an operation."""


@dataclass(frozen=True, slots=True)
class NotificationView:
    notification_id: int
    priority: str
    category: str
    content: str
    delivered_at: str


@dataclass(frozen=True, slots=True)
class DeviceView:
    device_id: str
    name: str
    transport: str
    local_actuators: bool


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    client_id: str
    profile_id: str
    role: str
    provider: str
    model: str
    uptime_seconds: int
    proactive_enabled: bool
    local_actions_enabled: bool
    voice_enabled: bool
    tool_count: int
    devices: tuple[DeviceView, ...]
    notifications: tuple[NotificationView, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VoiceTurn:
    transcript: str
    reply: str
    speech_warning: str | None = None


class _LazyVisionService:
    """Load large local vision models only for an explicit vision request."""

    def __init__(self, config: VisionConfig) -> None:
        self._config = config
        self._service: LocalVisionService | None = None

    @property
    def description(self) -> str:
        return "local one-frame analysis; models load on first explicit request"

    def _get(self) -> LocalVisionService:
        if self._service is None:
            self._service = LocalVisionService(self._config)
        return self._service

    def analyze_image(self, path: Any):
        return self._get().analyze_image(path)

    def capture_and_analyze(self):
        return self._get().capture_and_analyze()

    def close(self) -> None:
        if self._service is not None:
            self._service.close()


class DashboardSession:
    """Run the local agent only for explicit text, voice, or refresh requests."""

    def __init__(
        self,
        agent: Agent,
        *,
        profile_id: str = "owner",
        notification_limit: int = 50,
        memory_store: LocalMemoryStore | None = None,
        vision_service: _LazyVisionService | None = None,
        robotics_service: RoboticsService | None = None,
        voice_factory: Callable[[Callable[[], bool]], VoiceSession] | None = None,
        wake_detector_factory: Callable[[], Any] | None = None,
        warnings: tuple[str, ...] = (),
    ) -> None:
        if not 1 <= notification_limit <= 100:
            raise ValueError("Dashboard notification limit must be between 1 and 100.")
        self._agent = agent
        self._profile_id = profile_id
        self._notification_limit = notification_limit
        self._memory_store = memory_store
        self._vision_service = vision_service
        self._robotics_service = robotics_service
        self._voice_factory = voice_factory
        self._wake_detector_factory = wake_detector_factory
        self._voice_session: VoiceSession | None = None
        self._voice_cancel = Event()
        self._wake_detector: Any | None = None
        self._started_at = time.monotonic()
        self._warnings = warnings
        self._closed = False

    @property
    def voice_available(self) -> bool:
        return self._voice_factory is not None

    @property
    def wake_available(self) -> bool:
        return self._wake_detector_factory is not None

    @property
    def tool_descriptions(self) -> tuple[str, ...]:
        return self._agent.tool_descriptions

    def refresh(self) -> DashboardSnapshot:
        notifications: tuple[NotificationView, ...] = ()
        if self._memory_store is not None:
            notifications = tuple(
                NotificationView(
                    notification_id=item.id,
                    priority=item.priority,
                    category=item.category,
                    content=item.content,
                    delivered_at=item.delivered_at,
                )
                for item in self._memory_store.list_notifications(
                    self._notification_limit
                )
            )

        devices: tuple[DeviceView, ...] = ()
        if self._robotics_service is not None:
            devices = tuple(
                DeviceView(
                    device_id=item.device_id,
                    name=item.name,
                    transport=item.transport,
                    local_actuators=item.actuators_enabled,
                )
                for item in self._robotics_service.list_devices()
            )

        return DashboardSnapshot(
            client_id=socket.gethostname(),
            profile_id=self._profile_id,
            role="owner",
            provider=self._agent.provider_name,
            model=self._agent.model_name,
            uptime_seconds=max(0, int(time.monotonic() - self._started_at)),
            proactive_enabled=False,
            local_actions_enabled=bool(self._agent.tool_descriptions),
            voice_enabled=self.voice_available,
            tool_count=len(self._agent.tool_descriptions),
            devices=devices,
            notifications=notifications,
            warnings=self._warnings,
        )

    def send_message(self, message: str) -> str:
        text = message.strip()
        if not text or len(text) > 4_000:
            raise DashboardError("Message must contain 1 to 4000 characters.")
        reply = "".join(self._agent.stream_turn(text)).strip()
        if not reply:
            raise DashboardError("The model returned an empty reply.")
        return reply

    def voice_turn(self) -> VoiceTurn:
        if self._voice_factory is None:
            raise VoiceError("Voice mode is disabled in the configuration.")
        if self._voice_cancel.is_set():
            raise VoiceError("Voice capture cancelled.")
        if self._voice_session is None:
            self._voice_session = self._voice_factory(self._voice_cancel.is_set)
        transcript = self._voice_session.listen().strip()
        if self._voice_cancel.is_set():
            raise VoiceError("Voice capture cancelled.")
        if not transcript:
            raise VoiceError("No speech was recognized. Please try again.")
        reply = self.send_message(transcript)
        speech_warning = None
        if not self._voice_cancel.is_set():
            try:
                self._voice_session.speak(reply)
            except VoiceError as error:
                speech_warning = str(error)
        return VoiceTurn(transcript, reply, speech_warning)

    def begin_voice_mode(self) -> None:
        self._voice_cancel.clear()

    def end_voice_mode(self) -> None:
        self._voice_cancel.set()

    def wait_for_return_phrase(self) -> None:
        if self._wake_detector_factory is None:
            raise WakeError("Idle wake listening is unavailable.")
        if self._wake_detector is None:
            self._wake_detector = self._wake_detector_factory()
        self._wake_detector.wait()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._voice_cancel.set()
        if self._vision_service is not None:
            self._vision_service.close()
        if self._robotics_service is not None:
            self._robotics_service.close()


def create_dashboard_session(
    config: AppConfig,
    *,
    confirmer: Confirmer | None,
) -> DashboardSession:
    """Build the terminal agent's local capabilities without background monitors."""

    robotics_service: RoboticsService | None = None
    warnings: list[str] = []
    try:
        chat_api_key = resolve_api_key(config.ai)
        provider = create_provider(config.ai, api_key=chat_api_key)
        memory_store = None
        if config.memory.enabled:
            if config.memory.database_path is None:
                raise DashboardError("Local memory has no configured database path.")
            memory_store = LocalMemoryStore(
                config.memory.database_path,
                profile_id=config.memory.profile_id,
                profile_name=config.memory.profile_name,
            )

        vision_service = (
            _LazyVisionService(config.vision) if config.vision.enabled else None
        )

        if config.robotics.enabled:
            try:
                robotics_service = RoboticsService(config.robotics)
            except RoboticsError as error:
                warnings.append(f"Robotics unavailable: {error}")

        definitions: list[ToolDefinition] = []
        if config.tools.enabled:
            definitions.extend(build_computer_tool_definitions(config.tools))
        if memory_store is not None:
            definitions.extend(build_memory_tool_definitions(memory_store))
        if vision_service is not None:
            definitions.extend(build_vision_tool_definitions(vision_service))
        if robotics_service is not None:
            definitions.extend(build_robotics_tool_definitions(robotics_service))

        runtime = None
        if definitions:
            runtime = ToolRuntime(
                definitions,
                confirmer=confirmer,
                max_rounds=config.tools.max_rounds,
            )
        agent = Agent(
            provider,
            build_system_prompt(config.assistant),
            tool_runtime=runtime,
            conversation_store=memory_store,
            context_limit=config.memory.conversation_context_messages,
        )

        voice_factory = None
        if config.voice.enabled:
            speech_api_key = os.environ.get("GROQ_API_KEY")
            if not speech_api_key and config.ai.provider.casefold() == "groq":
                speech_api_key = chat_api_key
            voice_factory = lambda stop_requested: create_silence_stopping_voice_session(
                config.voice,
                config.wake,
                api_key=speech_api_key,
                stop_requested=stop_requested,
            )

        wake_factory = None
        if config.wake.enabled and config.wake.model_path is not None:
            wake_factory = lambda: VoskWakeDetector(
                model_path=config.wake.model_path,
                sample_rate=config.wake.sample_rate,
                phrases=(config.dashboard.wake_phrase,),
            )

        return DashboardSession(
            agent,
            profile_id=config.memory.profile_id,
            notification_limit=config.dashboard.notification_limit,
            memory_store=memory_store,
            vision_service=vision_service,
            robotics_service=robotics_service,
            voice_factory=voice_factory,
            wake_detector_factory=wake_factory,
            warnings=tuple(warnings),
        )
    except (DashboardError, MemoryStoreError):
        if robotics_service is not None:
            robotics_service.close()
        raise
    except Exception as error:
        if robotics_service is not None:
            robotics_service.close()
        raise DashboardError(str(error)) from error
