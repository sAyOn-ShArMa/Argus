"""Local SQLite persistence for conversation, memories, tasks, and reminders."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from argus.ai.provider import Message


class MemoryStoreError(RuntimeError):
    """The local memory database could not complete an operation."""


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    id: int
    role: str
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    category: str
    content: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: int
    title: str
    status: str
    due_at: str | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class ReminderRecord:
    id: int
    content: str
    remind_at: str
    category: str
    priority: str
    status: str
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class CalendarEventRecord:
    id: int
    title: str
    start_at: str
    priority: str
    status: str
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    id: int
    event_key: str
    category: str
    priority: str
    content: str
    delivered_at: str


class LocalMemoryStore:
    """A profile-scoped SQLite store with atomic conversation turns."""

    def __init__(
        self,
        database_path: Path,
        *,
        profile_id: str,
        profile_name: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.profile_id = profile_id
        self.profile_name = profile_name
        self._now = now or (lambda: datetime.now(timezone.utc))
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except (OSError, sqlite3.Error) as error:
            raise MemoryStoreError(f"Could not initialize local memory: {error}") from error

    def _timestamp(self) -> str:
        moment = self._now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.isoformat(timespec="seconds")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.database_path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            if connection is not None:
                connection.rollback()
            raise MemoryStoreError(f"Local memory operation failed: {error}") from error
        finally:
            if connection is not None:
                connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profile_state (
                    profile_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (profile_id, key),
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS conversation_profile_id
                ON conversation_messages(profile_id, id);

                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    category TEXT NOT NULL CHECK (
                        category IN ('fact', 'preference', 'project', 'other')
                    ),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS memories_profile_id
                ON memories(profile_id, id);

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'completed')),
                    due_at TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS tasks_profile_status
                ON tasks(profile_id, status, id);

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'completed')),
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS reminders_profile_status
                ON reminders(profile_id, status, remind_at);

                CREATE TABLE IF NOT EXISTS calendar_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    priority TEXT NOT NULL CHECK (
                        priority IN ('low', 'normal', 'high', 'critical')
                    ),
                    status TEXT NOT NULL CHECK (status IN ('pending', 'completed')),
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS calendar_profile_status
                ON calendar_events(profile_id, status, start_at);

                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    priority TEXT NOT NULL CHECK (
                        priority IN ('low', 'normal', 'high', 'critical')
                    ),
                    content TEXT NOT NULL,
                    delivered_at TEXT NOT NULL,
                    UNIQUE(profile_id, event_key),
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS notifications_profile_time
                ON notification_deliveries(profile_id, delivered_at);
                """
            )
            reminder_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(reminders)").fetchall()
            }
            if "category" not in reminder_columns:
                connection.execute(
                    "ALTER TABLE reminders ADD COLUMN "
                    "category TEXT NOT NULL DEFAULT 'reminder'"
                )
            if "priority" not in reminder_columns:
                connection.execute(
                    "ALTER TABLE reminders ADD COLUMN "
                    "priority TEXT NOT NULL DEFAULT 'normal'"
                )
            connection.execute(
                """
                INSERT INTO profiles(id, display_name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name
                """,
                (self.profile_id, self.profile_name, self._timestamp()),
            )

    @staticmethod
    def _require_text(value: str, label: str, maximum: int) -> str:
        text = value.strip()
        if not text:
            raise ValueError(f"{label} cannot be empty.")
        if len(text) > maximum:
            raise ValueError(f"{label} cannot exceed {maximum} characters.")
        return text

    @staticmethod
    def _require_priority(value: str) -> str:
        if value not in {"low", "normal", "high", "critical"}:
            raise ValueError("Priority must be low, normal, high, or critical.")
        return value

    @staticmethod
    def _require_iso_datetime(value: str, label: str) -> str:
        when = LocalMemoryStore._require_text(value, label, 100)
        try:
            datetime.fromisoformat(when)
        except ValueError as error:
            raise ValueError(f"{label} must be a valid ISO date and time.") from error
        return when

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        user = self._require_text(user_text, "User message", 20_000)
        assistant = self._require_text(assistant_text, "Assistant message", 20_000)
        timestamp = self._timestamp()
        with self._connection() as connection:
            connection.executemany(
                """
                INSERT INTO conversation_messages(profile_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (self.profile_id, "user", user, timestamp),
                    (self.profile_id, "assistant", assistant, timestamp),
                ),
            )

    def _context_cutoff(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            """
            SELECT value FROM profile_state
            WHERE profile_id = ? AND key = 'conversation_cutoff_id'
            """,
            (self.profile_id,),
        ).fetchone()
        if row is None:
            return 0
        try:
            return max(0, int(row["value"]))
        except (TypeError, ValueError):
            return 0

    def load_context(self, limit: int) -> list[Message]:
        if not 1 <= limit <= 100:
            raise ValueError("Context limit must be between 1 and 100.")
        with self._connection() as connection:
            cutoff = self._context_cutoff(connection)
            rows = connection.execute(
                """
                SELECT role, content FROM conversation_messages
                WHERE profile_id = ? AND id > ?
                ORDER BY id DESC LIMIT ?
                """,
                (self.profile_id, cutoff, limit),
            ).fetchall()
        rows.reverse()
        return [
            {"role": row["role"], "content": row["content"]}
            for row in rows
        ]

    def list_conversation(self, limit: int = 20) -> list[ConversationRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("History limit must be between 1 and 100.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, role, content, created_at FROM conversation_messages
                WHERE profile_id = ? ORDER BY id DESC LIMIT ?
                """,
                (self.profile_id, limit),
            ).fetchall()
        rows.reverse()
        return [ConversationRecord(**dict(row)) for row in rows]

    def reset_context(self) -> None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(id), 0) AS maximum FROM conversation_messages
                WHERE profile_id = ?
                """,
                (self.profile_id,),
            ).fetchone()
            cutoff = int(row["maximum"])
            connection.execute(
                """
                INSERT INTO profile_state(profile_id, key, value)
                VALUES (?, 'conversation_cutoff_id', ?)
                ON CONFLICT(profile_id, key) DO UPDATE SET value = excluded.value
                """,
                (self.profile_id, str(cutoff)),
            )

    def clear_conversation(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM conversation_messages WHERE profile_id = ?",
                (self.profile_id,),
            )
            connection.execute(
                """
                INSERT INTO profile_state(profile_id, key, value)
                VALUES (?, 'conversation_cutoff_id', '0')
                ON CONFLICT(profile_id, key) DO UPDATE SET value = '0'
                """,
                (self.profile_id,),
            )
            return cursor.rowcount

    def add_memory(self, content: str, category: str = "fact") -> MemoryRecord:
        text = self._require_text(content, "Memory", 2_000)
        if category not in {"fact", "preference", "project", "other"}:
            raise ValueError("Unknown memory category.")
        timestamp = self._timestamp()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memories(profile_id, category, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.profile_id, category, text, timestamp, timestamp),
            )
            memory_id = int(cursor.lastrowid)
        return MemoryRecord(memory_id, category, text, timestamp, timestamp)

    @staticmethod
    def _like_pattern(query: str) -> str:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def list_memories(self, limit: int = 20) -> list[MemoryRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("Memory limit must be between 1 and 100.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, category, content, created_at, updated_at FROM memories
                WHERE profile_id = ? ORDER BY id DESC LIMIT ?
                """,
                (self.profile_id, limit),
            ).fetchall()
        return [MemoryRecord(**dict(row)) for row in rows]

    def search_memories(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        text = self._require_text(query, "Search query", 200)
        if not 1 <= limit <= 20:
            raise ValueError("Search limit must be between 1 and 20.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, category, content, created_at, updated_at FROM memories
                WHERE profile_id = ? AND content LIKE ? ESCAPE '\\' COLLATE NOCASE
                ORDER BY id DESC LIMIT ?
                """,
                (self.profile_id, self._like_pattern(text), limit),
            ).fetchall()
        return [MemoryRecord(**dict(row)) for row in rows]

    def delete_memory(self, memory_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE profile_id = ? AND id = ?",
                (self.profile_id, memory_id),
            )
            return cursor.rowcount == 1

    def add_task(self, title: str, due_at: str | None = None) -> TaskRecord:
        text = self._require_text(title, "Task", 1_000)
        timestamp = self._timestamp()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks(profile_id, title, status, due_at, created_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (self.profile_id, text, due_at, timestamp),
            )
            task_id = int(cursor.lastrowid)
        return TaskRecord(task_id, text, "pending", due_at, timestamp, None)

    def list_tasks(self, *, include_completed: bool = False, limit: int = 50) -> list[TaskRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("Task limit must be between 1 and 100.")
        status_clause = "" if include_completed else "AND status = 'pending'"
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, title, status, due_at, created_at, completed_at FROM tasks
                WHERE profile_id = ? {status_clause}
                ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT ?
                """,
                (self.profile_id, limit),
            ).fetchall()
        return [TaskRecord(**dict(row)) for row in rows]

    def complete_task(self, task_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks SET status = 'completed', completed_at = ?
                WHERE profile_id = ? AND id = ? AND status = 'pending'
                """,
                (self._timestamp(), self.profile_id, task_id),
            )
            return cursor.rowcount == 1

    def delete_task(self, task_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM tasks WHERE profile_id = ? AND id = ?",
                (self.profile_id, task_id),
            )
            return cursor.rowcount == 1

    def add_reminder(
        self,
        content: str,
        remind_at: str,
        *,
        category: str = "reminder",
        priority: str = "normal",
    ) -> ReminderRecord:
        text = self._require_text(content, "Reminder", 1_000)
        when = self._require_iso_datetime(remind_at, "Reminder time")
        if category not in {"reminder", "deadline"}:
            raise ValueError("Reminder category must be reminder or deadline.")
        priority = self._require_priority(priority)
        timestamp = self._timestamp()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reminders(
                    profile_id, content, remind_at, category, priority,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (self.profile_id, text, when, category, priority, timestamp),
            )
            reminder_id = int(cursor.lastrowid)
        return ReminderRecord(
            reminder_id,
            text,
            when,
            category,
            priority,
            "pending",
            timestamp,
            None,
        )

    def list_reminders(
        self, *, include_completed: bool = False, limit: int = 50
    ) -> list[ReminderRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("Reminder limit must be between 1 and 100.")
        status_clause = "" if include_completed else "AND status = 'pending'"
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, content, remind_at, category, priority, status,
                       created_at, completed_at
                FROM reminders WHERE profile_id = ? {status_clause}
                ORDER BY remind_at, id LIMIT ?
                """,
                (self.profile_id, limit),
            ).fetchall()
        return [ReminderRecord(**dict(row)) for row in rows]

    def complete_reminder(self, reminder_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders SET status = 'completed', completed_at = ?
                WHERE profile_id = ? AND id = ? AND status = 'pending'
                """,
                (self._timestamp(), self.profile_id, reminder_id),
            )
            return cursor.rowcount == 1

    def delete_reminder(self, reminder_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM reminders WHERE profile_id = ? AND id = ?",
                (self.profile_id, reminder_id),
            )
            return cursor.rowcount == 1

    def add_calendar_event(
        self, title: str, start_at: str, *, priority: str = "normal"
    ) -> CalendarEventRecord:
        text = self._require_text(title, "Calendar event", 1_000)
        when = self._require_iso_datetime(start_at, "Calendar start time")
        priority = self._require_priority(priority)
        timestamp = self._timestamp()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO calendar_events(
                    profile_id, title, start_at, priority, status, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (self.profile_id, text, when, priority, timestamp),
            )
            event_id = int(cursor.lastrowid)
        return CalendarEventRecord(
            event_id, text, when, priority, "pending", timestamp, None
        )

    def list_calendar_events(
        self, *, include_completed: bool = False, limit: int = 50
    ) -> list[CalendarEventRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("Calendar event limit must be between 1 and 100.")
        status_clause = "" if include_completed else "AND status = 'pending'"
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, title, start_at, priority, status, created_at, completed_at
                FROM calendar_events
                WHERE profile_id = ? {status_clause}
                ORDER BY start_at, id LIMIT ?
                """,
                (self.profile_id, limit),
            ).fetchall()
        return [CalendarEventRecord(**dict(row)) for row in rows]

    def complete_calendar_event(self, event_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE calendar_events SET status = 'completed', completed_at = ?
                WHERE profile_id = ? AND id = ? AND status = 'pending'
                """,
                (self._timestamp(), self.profile_id, event_id),
            )
            return cursor.rowcount == 1

    def delete_calendar_event(self, event_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM calendar_events WHERE profile_id = ? AND id = ?",
                (self.profile_id, event_id),
            )
            return cursor.rowcount == 1

    def claim_notification(
        self,
        event_key: str,
        category: str,
        priority: str,
        content: str,
    ) -> bool:
        key = self._require_text(event_key, "Notification event key", 300)
        category = self._require_text(category, "Notification category", 50)
        priority = self._require_priority(priority)
        text = self._require_text(content, "Notification content", 2_000)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO notification_deliveries(
                    profile_id, event_key, category, priority, content, delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.profile_id,
                    key,
                    category,
                    priority,
                    text,
                    self._timestamp(),
                ),
            )
            return cursor.rowcount == 1

    def list_notifications(self, limit: int = 20) -> list[NotificationRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("Notification limit must be between 1 and 100.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, event_key, category, priority, content, delivered_at
                FROM notification_deliveries
                WHERE profile_id = ? ORDER BY id DESC LIMIT ?
                """,
                (self.profile_id, limit),
            ).fetchall()
        return [NotificationRecord(**dict(row)) for row in rows]

    def list_notifications_after(
        self, after_id: int = 0, *, limit: int = 20
    ) -> list[NotificationRecord]:
        if after_id < 0:
            raise ValueError("Notification cursor cannot be negative.")
        if not 1 <= limit <= 100:
            raise ValueError("Notification limit must be between 1 and 100.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, event_key, category, priority, content, delivered_at
                FROM notification_deliveries
                WHERE profile_id = ? AND id > ? ORDER BY id LIMIT ?
                """,
                (self.profile_id, after_id, limit),
            ).fetchall()
        return [NotificationRecord(**dict(row)) for row in rows]

    def clear_notifications(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM notification_deliveries WHERE profile_id = ?",
                (self.profile_id,),
            )
            return cursor.rowcount

    def counts(self) -> dict[str, int]:
        with self._connection() as connection:
            values: dict[str, int] = {}
            for label, table, condition in (
                ("conversation_messages", "conversation_messages", ""),
                ("memories", "memories", ""),
                ("pending_tasks", "tasks", "AND status = 'pending'"),
                ("pending_reminders", "reminders", "AND status = 'pending'"),
                (
                    "pending_calendar_events",
                    "calendar_events",
                    "AND status = 'pending'",
                ),
            ):
                row = connection.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE profile_id = ? {condition}",
                    (self.profile_id,),
                ).fetchone()
                values[label] = int(row["count"])
        return values
