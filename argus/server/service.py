"""Central profile-scoped Argus service with no remote tool execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Lock
import time

from argus import __version__
from argus.ai.factory import create_provider
from argus.ai.provider import ModelProvider
from argus.config import AppConfig, ServerClientConfig
from argus.core import Agent
from argus.memory import LocalMemoryStore
from argus.proactive import (
    LocalSystemReader,
    Notification,
    ProactiveEngine,
    ProactiveMonitor,
)
from argus.prompts import build_system_prompt


class RemotePermissionError(RuntimeError):
    """The authenticated role cannot perform the requested server operation."""


@dataclass(slots=True)
class _ProfileSession:
    store: LocalMemoryStore
    agent: Agent
    lock: Lock
    monitor: ProactiveMonitor | None = None


ProviderFactory = Callable[[str], ModelProvider]
NotificationSink = Callable[[str, Notification], None]


class ServerService:
    """Own profile sessions while keeping all local/action tools unavailable."""

    def __init__(
        self,
        config: AppConfig,
        *,
        api_key: str | None,
        provider_factory: ProviderFactory | None = None,
        notification_sink: NotificationSink | None = None,
    ) -> None:
        if not config.server.enabled:
            raise ValueError("Server mode is disabled in the configuration.")
        if not config.memory.enabled or config.memory.database_path is None:
            raise ValueError("Server mode requires local memory.")
        self._config = config
        self._clients = {
            client.client_id: client for client in config.server.clients
        }
        self._profiles: dict[str, _ProfileSession] = {}
        self._started_at = datetime.now(timezone.utc)
        self._started_clock = time.monotonic()
        provider_factory = provider_factory or (
            lambda profile_id: create_provider(config.ai, api_key=api_key)
        )
        notification_sink = notification_sink or (
            lambda profile_id, notification: None
        )

        for client in config.server.clients:
            if client.profile_id in self._profiles:
                continue
            store = LocalMemoryStore(
                config.memory.database_path,
                profile_id=client.profile_id,
                profile_name=client.display_name,
            )
            provider = provider_factory(client.profile_id)
            system_prompt = (
                build_system_prompt(config.assistant)
                + "\nServer mode: You are answering an authenticated remote text "
                "client. No computer, vision, robotics, memory-write, or other "
                "action tools are available in this mode. Never claim to have "
                "performed a local action."
            )
            agent = Agent(
                provider,
                system_prompt,
                conversation_store=store,
                context_limit=config.memory.conversation_context_messages,
            )
            monitor = None
            if config.proactive.enabled:
                engine = ProactiveEngine(
                    store,
                    config.proactive,
                    notifier=lambda notification, profile_id=client.profile_id: (
                        notification_sink(profile_id, notification)
                    ),
                    system_reader=LocalSystemReader(
                        config.memory.database_path.parent
                    ),
                )
                monitor = ProactiveMonitor(engine)
            self._profiles[client.profile_id] = _ProfileSession(
                store=store,
                agent=agent,
                lock=Lock(),
                monitor=monitor,
            )

    def _profile(self, client: ServerClientConfig) -> _ProfileSession:
        return self._profiles[client.profile_id]

    def start(self) -> None:
        for profile in self._profiles.values():
            if profile.monitor is not None:
                profile.monitor.start()

    def close(self) -> None:
        for profile in self._profiles.values():
            if profile.monitor is not None:
                profile.monitor.stop()

    def health(self) -> dict[str, object]:
        return {
            "ok": True,
            "service": "argus",
            "version": __version__,
            "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def status(self, client: ServerClientConfig) -> dict[str, object]:
        return {
            "ok": True,
            "client_id": client.client_id,
            "profile_id": client.profile_id,
            "role": client.role,
            "provider": self._config.ai.provider,
            "model": self._config.ai.model,
            "uptime_seconds": int(time.monotonic() - self._started_clock),
            "started_at": self._started_at.isoformat(timespec="seconds"),
            "proactive_enabled": self._config.proactive.enabled,
            "remote_capabilities": [
                "chat",
                "status",
                "delivered_notifications",
                "configured_device_list",
            ],
            "remote_actions_enabled": False,
        }

    def chat(self, client: ServerClientConfig, message: str) -> str:
        if client.role == "read_only":
            raise RemotePermissionError("This client role is read-only.")
        text = message.strip()
        if not text or len(text) > 4_000:
            raise ValueError("Message must contain 1 to 4000 characters.")
        profile = self._profile(client)
        with profile.lock:
            return "".join(profile.agent.stream_turn(text)).strip()

    def notifications(
        self, client: ServerClientConfig, *, after_id: int, limit: int
    ) -> list[dict[str, object]]:
        profile = self._profile(client)
        records = profile.store.list_notifications_after(after_id, limit=limit)
        return [asdict(record) for record in records]

    def devices(self, client: ServerClientConfig) -> list[dict[str, object]]:
        return [
            {
                "device_id": device.device_id,
                "name": device.name,
                "transport": device.transport,
                "actuators_enabled_locally": device.actuators_enabled,
                "remote_control_enabled": False,
            }
            for device in self._config.robotics.devices
        ]
