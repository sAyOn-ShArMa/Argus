from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from argus.memory import LocalMemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "nested" / "argus.db"
        moment = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        self.store = LocalMemoryStore(
            self.path,
            profile_id="owner",
            profile_name="Owner",
            now=lambda: moment,
        )

    def test_completed_turn_survives_store_restart(self) -> None:
        self.store.append_turn("My project is Helios.", "Noted, sir.")

        reopened = LocalMemoryStore(
            self.path, profile_id="owner", profile_name="Owner"
        )

        self.assertEqual(
            reopened.load_context(20),
            [
                {"role": "user", "content": "My project is Helios."},
                {"role": "assistant", "content": "Noted, sir."},
            ],
        )

    def test_reset_starts_fresh_context_without_erasing_history(self) -> None:
        self.store.append_turn("First", "Reply")

        self.store.reset_context()

        self.assertEqual(self.store.load_context(20), [])
        self.assertEqual(len(self.store.list_conversation()), 2)
        self.store.append_turn("Second", "New reply")
        self.assertEqual(
            [message["content"] for message in self.store.load_context(20)],
            ["Second", "New reply"],
        )

    def test_clear_conversation_permanently_removes_only_history(self) -> None:
        self.store.append_turn("First", "Reply")
        self.store.add_memory("Prefers concise answers", "preference")

        deleted = self.store.clear_conversation()

        self.assertEqual(deleted, 2)
        self.assertEqual(self.store.list_conversation(), [])
        self.assertEqual(len(self.store.list_memories()), 1)

    def test_profiles_are_isolated_in_one_database(self) -> None:
        self.store.add_memory("Owner fact")
        guest = LocalMemoryStore(
            self.path, profile_id="guest", profile_name="Guest"
        )

        self.assertEqual(guest.list_memories(), [])
        guest.add_memory("Guest fact")

        self.assertEqual(self.store.list_memories()[0].content, "Owner fact")

    def test_memory_search_treats_sql_wildcards_as_literal_text(self) -> None:
        self.store.add_memory("Battery reached 100%")
        self.store.add_memory("Battery reached 50 percent")

        results = self.store.search_memories("100%")

        self.assertEqual([record.content for record in results], ["Battery reached 100%"])

    def test_task_and_reminder_lifecycle(self) -> None:
        task = self.store.add_task("Test the robot")
        reminder = self.store.add_reminder(
            "Charge battery", "2026-08-16T09:00:00+05:45"
        )

        self.assertEqual(self.store.list_tasks()[0].id, task.id)
        self.assertEqual(self.store.list_reminders()[0].id, reminder.id)
        self.assertTrue(self.store.complete_task(task.id))
        self.assertTrue(self.store.complete_reminder(reminder.id))
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.store.list_reminders(), [])
        self.assertTrue(self.store.delete_task(task.id))
        self.assertTrue(self.store.delete_reminder(reminder.id))

    def test_calendar_and_notification_delivery_lifecycle(self) -> None:
        event = self.store.add_calendar_event(
            "Robotics meeting",
            "2026-08-16T14:00:00+05:45",
            priority="high",
        )

        first_claim = self.store.claim_notification(
            f"calendar:{event.id}",
            "calendar",
            "high",
            "Meeting soon",
        )
        duplicate_claim = self.store.claim_notification(
            f"calendar:{event.id}",
            "calendar",
            "high",
            "Meeting soon",
        )

        self.assertTrue(first_claim)
        self.assertFalse(duplicate_claim)
        self.assertEqual(self.store.list_calendar_events()[0].priority, "high")
        self.assertEqual(len(self.store.list_notifications()), 1)
        self.assertEqual(self.store.clear_notifications(), 1)
        self.assertEqual(self.store.list_notifications(), [])
        self.assertTrue(self.store.complete_calendar_event(event.id))
        self.assertTrue(self.store.delete_calendar_event(event.id))

    def test_existing_reminder_table_is_migrated_without_data_loss(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE profiles (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                INSERT INTO profiles VALUES ('owner', 'Owner', 'old');
                INSERT INTO reminders(
                    profile_id, content, remind_at, status, created_at
                ) VALUES (
                    'owner', 'Legacy reminder', '2026-08-16T09:00:00+05:45',
                    'pending', 'old'
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = LocalMemoryStore(
            legacy_path, profile_id="owner", profile_name="Owner"
        )
        record = migrated.list_reminders()[0]

        self.assertEqual(record.content, "Legacy reminder")
        self.assertEqual(record.category, "reminder")
        self.assertEqual(record.priority, "normal")


if __name__ == "__main__":
    unittest.main()
