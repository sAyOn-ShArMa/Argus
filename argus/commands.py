"""Safe, explicit local commands for the typed interface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from argus.config import AppConfig
from argus.core import Agent
from argus.memory import LocalMemoryStore
from argus.proactive import is_quiet_time


CommandAction = Literal[
    "voice_turn",
    "wake_mode",
    "delete_memory",
    "delete_task",
    "delete_reminder",
    "clear_history",
    "camera_once",
    "analyze_image",
    "device_status",
    "device_telemetry",
    "device_actuate",
    "device_estop",
    "delete_event",
    "check_notifications",
    "clear_notifications",
]


@dataclass(frozen=True, slots=True)
class DeviceActionRequest:
    device_id: str
    actuator: str | None = None
    value: int | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    handled: bool
    message: str | None = None
    should_exit: bool = False
    action: CommandAction | None = None
    payload: int | str | DeviceActionRequest | None = None


class CommandRouter:
    """Route slash commands without asking the model to interpret them."""

    def __init__(
        self,
        now: Callable[[], datetime] = datetime.now,
        *,
        memory_store: LocalMemoryStore | None = None,
    ) -> None:
        self._now = now
        self._memory = memory_store

    @staticmethod
    def _arguments(text: str) -> str:
        return text.partition(" ")[2].strip()

    @staticmethod
    def _positive_id(text: str, usage: str) -> int:
        try:
            value = int(text)
        except ValueError as error:
            raise ValueError(f"Usage: {usage}") from error
        if value < 1:
            raise ValueError(f"Usage: {usage}")
        return value

    @staticmethod
    def _preview(content: str, limit: int = 120) -> str:
        compact = " ".join(content.split())
        return compact if len(compact) <= limit else compact[: limit - 1] + "…"

    def _memory_required(self) -> LocalMemoryStore:
        if self._memory is None:
            raise ValueError("Local memory is disabled in the configuration.")
        return self._memory

    @staticmethod
    def _robotics_device(config: AppConfig, device_id: str):
        if not config.robotics.enabled:
            raise ValueError("Robotics and IoT are disabled in the configuration.")
        for device in config.robotics.devices:
            if device.device_id == device_id:
                return device
        raise ValueError(f"Device '{device_id}' is not allowlisted.")

    def _list_memories(self) -> CommandResult:
        records = self._memory_required().list_memories()
        if not records:
            return CommandResult(True, "No explicit memories stored yet.")
        lines = [
            f"#{record.id} [{record.category}] {self._preview(record.content)}"
            for record in records
        ]
        return CommandResult(True, "Stored memories:\n" + "\n".join(lines))

    def _list_tasks(self, arguments: str) -> CommandResult:
        include_completed = arguments.casefold() == "all"
        if arguments and not include_completed:
            raise ValueError("Usage: /tasks or /tasks all")
        records = self._memory_required().list_tasks(
            include_completed=include_completed
        )
        if not records:
            return CommandResult(True, "No tasks found.")
        lines = [
            f"#{record.id} [{'done' if record.status == 'completed' else 'pending'}] "
            f"{self._preview(record.title)}"
            for record in records
        ]
        return CommandResult(True, "Tasks:\n" + "\n".join(lines))

    def _route_task(self, arguments: str) -> CommandResult:
        action, _, value = arguments.partition(" ")
        action = action.casefold()
        value = value.strip()
        if action == "add" and value:
            record = self._memory_required().add_task(value)
            return CommandResult(True, f"Task #{record.id} added.")
        if action == "done":
            task_id = self._positive_id(value, "/task done <id>")
            completed = self._memory_required().complete_task(task_id)
            message = (
                f"Task #{task_id} completed."
                if completed
                else f"Pending task #{task_id} was not found."
            )
            return CommandResult(True, message)
        if action == "delete":
            task_id = self._positive_id(value, "/task delete <id>")
            return CommandResult(True, action="delete_task", payload=task_id)
        raise ValueError("Usage: /task add <text>, /task done <id>, or /task delete <id>")

    def _list_reminders(self, arguments: str) -> CommandResult:
        include_completed = arguments.casefold() == "all"
        if arguments and not include_completed:
            raise ValueError("Usage: /reminders or /reminders all")
        records = self._memory_required().list_reminders(
            include_completed=include_completed
        )
        if not records:
            return CommandResult(True, "No reminders found.")
        lines = [
            f"#{record.id} [{record.priority} {record.category} | {record.status}] "
            f"{record.remind_at}: "
            f"{self._preview(record.content)}"
            for record in records
        ]
        return CommandResult(True, "Stored reminders:\n" + "\n".join(lines))

    def _add_reminder(
        self, arguments: str, *, category: str = "reminder"
    ) -> CommandResult:
        parts = [part.strip() for part in arguments.split("|")]
        usage = (
            f"/{'deadline' if category == 'deadline' else 'remind'} "
            "YYYY-MM-DD HH:MM | [priority |] text"
        )
        if len(parts) == 2:
            when_text, content = parts
            priority = "high" if category == "deadline" else "normal"
        elif len(parts) == 3:
            when_text, priority, content = parts
        else:
            raise ValueError(f"Usage: {usage}")
        if not when_text or not content:
            raise ValueError(f"Usage: {usage}")
        if priority not in {"low", "normal", "high", "critical"}:
            raise ValueError("Priority must be low, normal, high, or critical.")
        try:
            local_time = datetime.strptime(when_text, "%Y-%m-%d %H:%M")
        except ValueError as error:
            raise ValueError(
                "Reminder time must use YYYY-MM-DD HH:MM, for example "
                "/remind 2026-08-16 09:00 | Check the robot battery"
            ) from error
        timezone = self._now().astimezone().tzinfo
        remind_at = local_time.replace(tzinfo=timezone).isoformat(timespec="minutes")
        record = self._memory_required().add_reminder(
            content,
            remind_at,
            category=category,
            priority=priority,
        )
        return CommandResult(
            True,
            f"{category.title()} #{record.id} scheduled for {record.remind_at} "
            f"with {priority} priority. Notifications run only while Argus is open "
            "and respect quiet hours.",
        )

    def _route_reminder(self, arguments: str) -> CommandResult:
        action, _, value = arguments.partition(" ")
        action = action.casefold()
        if action == "done":
            reminder_id = self._positive_id(value.strip(), "/reminder done <id>")
            completed = self._memory_required().complete_reminder(reminder_id)
            message = (
                f"Reminder #{reminder_id} completed."
                if completed
                else f"Pending reminder #{reminder_id} was not found."
            )
            return CommandResult(True, message)
        if action == "delete":
            reminder_id = self._positive_id(value.strip(), "/reminder delete <id>")
            return CommandResult(True, action="delete_reminder", payload=reminder_id)
        raise ValueError("Usage: /reminder done <id> or /reminder delete <id>")

    def _list_calendar_events(self, arguments: str) -> CommandResult:
        include_completed = arguments.casefold() == "all"
        if arguments and not include_completed:
            raise ValueError("Usage: /events or /events all")
        records = self._memory_required().list_calendar_events(
            include_completed=include_completed
        )
        if not records:
            return CommandResult(True, "No calendar events found.")
        lines = [
            f"#{record.id} [{record.priority} | {record.status}] "
            f"{record.start_at}: {self._preview(record.title)}"
            for record in records
        ]
        return CommandResult(True, "Local calendar:\n" + "\n".join(lines))

    def _add_calendar_event(self, arguments: str) -> CommandResult:
        parts = [part.strip() for part in arguments.split("|")]
        usage = "/event add YYYY-MM-DD HH:MM | [priority |] title"
        if len(parts) == 2:
            when_text, title = parts
            priority = "normal"
        elif len(parts) == 3:
            when_text, priority, title = parts
        else:
            raise ValueError(f"Usage: {usage}")
        if not when_text or not title:
            raise ValueError(f"Usage: {usage}")
        if priority not in {"low", "normal", "high", "critical"}:
            raise ValueError("Priority must be low, normal, high, or critical.")
        try:
            local_time = datetime.strptime(when_text, "%Y-%m-%d %H:%M")
        except ValueError as error:
            raise ValueError(f"Usage: {usage}") from error
        timezone = self._now().astimezone().tzinfo
        start_at = local_time.replace(tzinfo=timezone).isoformat(timespec="minutes")
        record = self._memory_required().add_calendar_event(
            title, start_at, priority=priority
        )
        return CommandResult(
            True,
            f"Calendar event #{record.id} scheduled for {record.start_at} "
            f"with {priority} priority.",
        )

    def _route_calendar_event(self, arguments: str) -> CommandResult:
        action, _, value = arguments.partition(" ")
        action = action.casefold()
        value = value.strip()
        if action == "add":
            return self._add_calendar_event(value)
        if action == "done":
            event_id = self._positive_id(value, "/event done <id>")
            completed = self._memory_required().complete_calendar_event(event_id)
            message = (
                f"Calendar event #{event_id} completed."
                if completed
                else f"Pending calendar event #{event_id} was not found."
            )
            return CommandResult(True, message)
        if action == "delete":
            event_id = self._positive_id(value, "/event delete <id>")
            return CommandResult(True, action="delete_event", payload=event_id)
        raise ValueError(
            "Usage: /event add <time> | <title>, /event done <id>, "
            "or /event delete <id>"
        )

    def _list_notifications(self, arguments: str) -> CommandResult:
        limit = 20
        if arguments:
            limit = self._positive_id(arguments, "/notifications [1-100]")
            if limit > 100:
                raise ValueError("Usage: /notifications [1-100]")
        records = self._memory_required().list_notifications(limit)
        if not records:
            return CommandResult(True, "No notifications have been delivered yet.")
        lines = []
        for record in records:
            delivered_at = record.delivered_at
            try:
                delivered_at = (
                    datetime.fromisoformat(delivered_at)
                    .astimezone()
                    .isoformat(timespec="minutes")
                )
            except ValueError:
                pass
            lines.append(
                f"#{record.id} [{record.priority} {record.category}] "
                f"{delivered_at}: {self._preview(record.content)}"
            )
        return CommandResult(True, "Delivered notifications:\n" + "\n".join(lines))

    def _history(self, arguments: str) -> CommandResult:
        limit = 20
        if arguments:
            limit = self._positive_id(arguments, "/history [1-100]")
            if limit > 100:
                raise ValueError("Usage: /history [1-100]")
        records = self._memory_required().list_conversation(limit)
        if not records:
            return CommandResult(True, "No conversation history stored yet.")
        lines = [
            f"#{record.id} {record.role.title()}: {self._preview(record.content)}"
            for record in records
        ]
        return CommandResult(True, "Recent history:\n" + "\n".join(lines))

    def route(self, text: str, *, agent: Agent, config: AppConfig) -> CommandResult:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return CommandResult(handled=False)

        command = stripped.split(maxsplit=1)[0].casefold()
        arguments = self._arguments(stripped)
        if command in {"/exit", "/quit"}:
            return CommandResult(True, "Until next time.", should_exit=True)
        if command == "/help":
            return CommandResult(
                True,
                "Commands: /status, /dashboard-status, /tools, "
                "/voice, /wake, "
                "/remember, /memories, "
                "/forget, /tasks, /task, /remind, /reminders, /reminder, /history, "
                "/clear-history, /camera, /vision, /vision-status, /devices, "
                "/device-status, /telemetry, /actuate, /estop, /robotics-status, "
                "/deadline, /events, /event, /notifications, "
                "/clear-notifications, /notifications-status, /check-alerts, "
                "/time, /date, /reset, /exit",
            )
        if command == "/status":
            turns = len(agent.history) // 2
            memory_status = "disabled"
            if self._memory is not None:
                counts = self._memory.counts()
                memory_status = (
                    f"{counts['memories']} memories, "
                    f"{counts['pending_tasks']} tasks, "
                    f"{counts['pending_reminders']} reminders"
                )
            return CommandResult(
                True,
                f"Provider: {agent.provider_name} | Model: {agent.model_name} | "
                f"Active context turns: {turns} | Memory: {memory_status} | "
                f"Voice: {'enabled' if config.voice.enabled else 'disabled'} | "
                f"Wake mode: {'enabled' if config.wake.enabled else 'disabled'} | "
                f"Vision: {'enabled' if config.vision.enabled else 'disabled'} | "
                f"Robotics: {'enabled' if config.robotics.enabled else 'disabled'} | "
                f"Proactive: {'enabled' if config.proactive.enabled else 'disabled'}",
            )
        if command == "/server-status":
            return CommandResult(
                True,
                "The Tier 9 remote text endpoint has been removed. Argus is local-only.",
            )
        if command == "/dashboard-status":
            if not config.dashboard.enabled:
                return CommandResult(True, "Tier 10 Control Center is disabled.")
            return CommandResult(
                True,
                "The local Control Center starts separately with python -m "
                "argus.dashboard. It runs tools only for a current user request, "
                f"locks after {config.dashboard.idle_timeout_seconds} seconds, and "
                "has no remote endpoint or automatic task monitor.",
            )
        if command == "/tools":
            descriptions = agent.tool_descriptions
            if not descriptions:
                return CommandResult(True, "Computer tools are disabled.")
            return CommandResult(True, "Available tools: " + "; ".join(descriptions))
        if command == "/voice":
            if not config.voice.enabled:
                return CommandResult(True, "Voice mode is disabled in the configuration.")
            return CommandResult(True, action="voice_turn")
        if command == "/wake":
            if not config.wake.enabled:
                return CommandResult(True, "Wake mode is disabled in the configuration.")
            return CommandResult(True, action="wake_mode")
        if command == "/camera":
            if not config.vision.enabled:
                return CommandResult(True, "Vision is disabled in the configuration.")
            return CommandResult(True, action="camera_once")
        if command == "/vision":
            if not config.vision.enabled:
                return CommandResult(True, "Vision is disabled in the configuration.")
            if not arguments:
                raise ValueError("Usage: /vision <approved image path>")
            return CommandResult(True, action="analyze_image", payload=arguments)
        if command == "/vision-status":
            if not config.vision.enabled:
                return CommandResult(True, "Vision is disabled in the configuration.")
            return CommandResult(
                True,
                "Vision is configured for explicit local one-frame analysis. "
                "Frames are not saved; face identity recognition is disabled.",
            )
        if command == "/robotics-status":
            if not config.robotics.enabled:
                return CommandResult(
                    True, "Robotics and IoT are disabled in the configuration."
                )
            transports = sorted(
                {device.transport for device in config.robotics.devices}
            )
            return CommandResult(
                True,
                f"{len(config.robotics.devices)} allowlisted device(s) via "
                f"{', '.join(transports)}. No background or autonomous control; "
                "actuator writes require a fresh confirmation.",
            )
        if command == "/devices":
            if not config.robotics.enabled:
                return CommandResult(
                    True, "Robotics and IoT are disabled in the configuration."
                )
            lines = []
            for device in config.robotics.devices:
                writes = (
                    "writes require confirmation"
                    if device.actuators_enabled
                    else "writes disabled"
                )
                lines.append(
                    f"{device.device_id}: {device.name} "
                    f"[{device.transport}; {writes}]"
                )
            return CommandResult(True, "Configured devices:\n" + "\n".join(lines))
        if command == "/device-status":
            if not arguments:
                raise ValueError("Usage: /device-status <device id>")
            device = self._robotics_device(config, arguments)
            return CommandResult(
                True,
                action="device_status",
                payload=DeviceActionRequest(device.device_id),
            )
        if command == "/telemetry":
            if not arguments:
                raise ValueError("Usage: /telemetry <device id>")
            device = self._robotics_device(config, arguments)
            return CommandResult(
                True,
                action="device_telemetry",
                payload=DeviceActionRequest(device.device_id),
            )
        if command == "/actuate":
            parts = arguments.split()
            if len(parts) != 3:
                raise ValueError(
                    "Usage: /actuate <device id> <actuator> <integer value>"
                )
            device_id, actuator, raw_value = parts
            device = self._robotics_device(config, device_id)
            if not device.actuators_enabled:
                raise ValueError(f"Actuator writes are disabled for '{device_id}'.")
            if actuator not in device.allowed_actuators:
                raise ValueError(
                    f"Actuator '{actuator}' is not allowlisted for '{device_id}'."
                )
            try:
                value = int(raw_value)
            except ValueError as error:
                raise ValueError("Actuator value must be an integer.") from error
            return CommandResult(
                True,
                action="device_actuate",
                payload=DeviceActionRequest(device_id, actuator, value),
            )
        if command == "/estop":
            if not arguments:
                raise ValueError("Usage: /estop <device id>")
            device = self._robotics_device(config, arguments)
            return CommandResult(
                True,
                action="device_estop",
                payload=DeviceActionRequest(device.device_id),
            )
        if command == "/remember":
            if not arguments:
                raise ValueError("Usage: /remember <fact to store>")
            record = self._memory_required().add_memory(arguments)
            return CommandResult(True, f"Memory #{record.id} stored locally.")
        if command == "/memories":
            return self._list_memories()
        if command == "/forget":
            memory_id = self._positive_id(arguments, "/forget <memory id>")
            return CommandResult(True, action="delete_memory", payload=memory_id)
        if command == "/tasks":
            return self._list_tasks(arguments)
        if command == "/task":
            return self._route_task(arguments)
        if command == "/remind":
            return self._add_reminder(arguments)
        if command == "/deadline":
            return self._add_reminder(arguments, category="deadline")
        if command == "/reminders":
            return self._list_reminders(arguments)
        if command == "/reminder":
            return self._route_reminder(arguments)
        if command == "/events":
            return self._list_calendar_events(arguments)
        if command == "/event":
            return self._route_calendar_event(arguments)
        if command == "/notifications":
            return self._list_notifications(arguments)
        if command == "/clear-notifications":
            self._memory_required()
            return CommandResult(True, action="clear_notifications")
        if command == "/notifications-status":
            if not config.proactive.enabled:
                return CommandResult(
                    True, "Proactive notifications are disabled in the configuration."
                )
            categories = ", ".join(config.proactive.enabled_categories)
            quiet_state = (
                "quiet now"
                if is_quiet_time(self._now().astimezone(), config.proactive)
                else "notifications allowed now"
            )
            return CommandResult(
                True,
                f"Proactive notifications are active while Argus is open. Poll: "
                f"{config.proactive.poll_interval_seconds}s | Quiet hours: "
                f"{config.proactive.quiet_hours_start}-"
                f"{config.proactive.quiet_hours_end} | Minimum priority: "
                f"{config.proactive.minimum_priority} | Categories: {categories} | "
                f"{quiet_state}.",
            )
        if command == "/check-alerts":
            if not config.proactive.enabled:
                return CommandResult(
                    True, "Proactive notifications are disabled in the configuration."
                )
            return CommandResult(True, action="check_notifications")
        if command == "/history":
            return self._history(arguments)
        if command == "/clear-history":
            self._memory_required()
            return CommandResult(True, action="clear_history")
        if command == "/time":
            return CommandResult(True, self._now().strftime("%I:%M %p %Z").strip())
        if command == "/date":
            return CommandResult(True, self._now().strftime("%A, %B %d, %Y"))
        if command == "/reset":
            agent.reset()
            return CommandResult(
                True,
                "Active conversation context cleared; stored history was retained.",
            )

        return CommandResult(True, f"Unknown command: {command}. Type /help.")
