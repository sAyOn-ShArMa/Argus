"""Quiet-hours-aware local notification scheduling and duplicate suppression."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
import math

from argus.config import ProactiveConfig
from argus.memory import LocalMemoryStore
from argus.proactive.interfaces import Notification, Notifier, SystemReader


_PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2, "critical": 3}


def _clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def is_quiet_time(moment: datetime, config: ProactiveConfig) -> bool:
    start = _clock(config.quiet_hours_start)
    end = _clock(config.quiet_hours_end)
    current = moment.timetz().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _local_datetime(value: str, now: datetime) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=now.tzinfo)
    if now.tzinfo is None:
        return parsed.replace(tzinfo=None)
    return parsed.astimezone(now.tzinfo)


class ProactiveEngine:
    """Evaluate due local events without performing external actions."""

    def __init__(
        self,
        store: LocalMemoryStore,
        config: ProactiveConfig,
        *,
        notifier: Notifier,
        system_reader: SystemReader,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._notifier = notifier
        self._system_reader = system_reader
        self._now = now or (lambda: datetime.now().astimezone())

    @property
    def config(self) -> ProactiveConfig:
        return self._config

    def _eligible(self, notification: Notification) -> bool:
        return (
            notification.category in self._config.enabled_categories
            and _PRIORITY_RANK[notification.priority]
            >= _PRIORITY_RANK[self._config.minimum_priority]
        )

    def _scheduled(self, now: datetime) -> list[Notification]:
        notifications: list[Notification] = []
        for reminder in self._store.list_reminders(limit=100):
            due = _local_datetime(reminder.remind_at, now)
            if due > now:
                continue
            category = (
                "deadlines" if reminder.category == "deadline" else "reminders"
            )
            label = "Deadline due" if category == "deadlines" else "Reminder"
            notifications.append(
                Notification(
                    event_key=f"reminder:{reminder.id}:{reminder.remind_at}",
                    category=category,
                    priority=reminder.priority,
                    message=f"{label}: {' '.join(reminder.content.split())}",
                )
            )

        lead_end = now + timedelta(minutes=self._config.calendar_lead_minutes)
        for event in self._store.list_calendar_events(limit=100):
            starts = _local_datetime(event.start_at, now)
            if starts > lead_end:
                continue
            seconds = (starts - now).total_seconds()
            if seconds > 0:
                minutes = max(1, math.ceil(seconds / 60))
                timing = f"in {minutes} minute{'s' if minutes != 1 else ''}"
            elif seconds > -60:
                timing = "now"
            else:
                timing = "due"
            notifications.append(
                Notification(
                    event_key=(
                        f"calendar:{event.id}:{event.start_at}:"
                        f"{self._config.calendar_lead_minutes}"
                    ),
                    category="calendar",
                    priority=event.priority,
                    message=(
                        f"Calendar event {timing}: {' '.join(event.title.split())}"
                    ),
                )
            )
        return notifications

    def _system(self, now: datetime) -> list[Notification]:
        snapshot = self._system_reader.snapshot()
        day = now.date().isoformat()
        notifications: list[Notification] = []
        if (
            snapshot.battery_percent is not None
            and snapshot.plugged_in is False
            and snapshot.battery_percent <= self._config.battery_warning_percent
        ):
            priority = "critical" if snapshot.battery_percent <= 5 else "high"
            notifications.append(
                Notification(
                    event_key=f"system:battery-low:{day}",
                    category="system",
                    priority=priority,
                    message=(
                        f"Battery is at {snapshot.battery_percent}% and not charging."
                    ),
                )
            )
        if (
            snapshot.disk_free_percent is not None
            and snapshot.disk_free_percent <= self._config.disk_free_warning_percent
        ):
            priority = "critical" if snapshot.disk_free_percent <= 3 else "high"
            notifications.append(
                Notification(
                    event_key=f"system:disk-low:{day}",
                    category="system",
                    priority=priority,
                    message=(
                        f"System disk has {snapshot.disk_free_percent:.1f}% free space."
                    ),
                )
            )
        return notifications

    def run_once(self) -> tuple[Notification, ...]:
        if not self._config.enabled:
            return ()
        now = self._now()
        if now.tzinfo is None:
            now = now.astimezone()
        if is_quiet_time(now, self._config):
            return ()

        candidates = [
            notification
            for notification in (*self._scheduled(now), *self._system(now))
            if self._eligible(notification)
        ]
        candidates.sort(
            key=lambda notification: (
                -_PRIORITY_RANK[notification.priority],
                notification.event_key,
            )
        )
        delivered: list[Notification] = []
        for notification in candidates:
            if len(delivered) >= self._config.max_notifications_per_cycle:
                break
            claimed = self._store.claim_notification(
                notification.event_key,
                notification.category,
                notification.priority,
                notification.message,
            )
            if not claimed:
                continue
            self._notifier(notification)
            delivered.append(notification)
        return tuple(delivered)
