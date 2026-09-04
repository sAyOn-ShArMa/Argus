"""Provider-independent notification and system-monitoring contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Notification:
    event_key: str
    category: str
    priority: str
    message: str

    def summary(self) -> str:
        return f"[{self.priority.upper()} | {self.category}] {self.message}"


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    battery_percent: int | None = None
    plugged_in: bool | None = None
    disk_free_percent: float | None = None


class SystemReader(Protocol):
    def snapshot(self) -> SystemSnapshot:
        """Return one bounded local system snapshot."""


class Notifier(Protocol):
    def __call__(self, notification: Notification) -> None:
        """Surface one already-filtered notification."""
