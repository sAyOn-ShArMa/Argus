from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from argus.config import ProactiveConfig
from argus.memory import LocalMemoryStore
from argus.proactive import ProactiveEngine, SystemSnapshot, is_quiet_time


NEPAL = timezone(timedelta(hours=5, minutes=45))


class FakeSystemReader:
    def __init__(self, snapshot: SystemSnapshot | None = None) -> None:
        self.value = snapshot or SystemSnapshot(
            battery_percent=80,
            plugged_in=True,
            disk_free_percent=50,
        )

    def snapshot(self) -> SystemSnapshot:
        return self.value


class ProactiveEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.moment = datetime(2026, 8, 16, 12, 0, tzinfo=NEPAL)
        self.store = LocalMemoryStore(
            Path(self.temporary.name) / "argus.db",
            profile_id="owner",
            profile_name="Owner",
            now=lambda: self.moment,
        )
        self.config = ProactiveConfig(enabled=True)
        self.delivered = []

    def engine(
        self,
        *,
        moment: datetime | None = None,
        config: ProactiveConfig | None = None,
        system: SystemSnapshot | None = None,
    ) -> ProactiveEngine:
        return ProactiveEngine(
            self.store,
            config or self.config,
            notifier=self.delivered.append,
            system_reader=FakeSystemReader(system),
            now=lambda: moment or self.moment,
        )

    def test_due_reminder_is_delivered_only_once_across_checks(self) -> None:
        self.store.add_reminder(
            "Charge the robot",
            (self.moment - timedelta(minutes=1)).isoformat(),
            priority="high",
        )
        engine = self.engine()

        first = engine.run_once()
        second = engine.run_once()

        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())
        self.assertIn("Charge the robot", first[0].message)
        self.assertEqual(len(self.store.list_notifications()), 1)

    def test_quiet_hours_delay_without_claiming_notification(self) -> None:
        late = self.moment.replace(hour=23)
        morning = (late + timedelta(days=1)).replace(hour=8)
        self.store.add_reminder(
            "Quiet-hours test",
            (late - timedelta(minutes=1)).isoformat(),
        )

        during_quiet = self.engine(moment=late).run_once()
        after_quiet = self.engine(moment=morning).run_once()

        self.assertEqual(during_quiet, ())
        self.assertEqual(len(after_quiet), 1)

    def test_calendar_event_is_announced_within_lead_window(self) -> None:
        self.store.add_calendar_event(
            "Robotics meeting",
            (self.moment + timedelta(minutes=10)).isoformat(),
        )

        delivered = self.engine().run_once()

        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].category, "calendar")
        self.assertIn("in 10 minutes", delivered[0].message)

    def test_disabled_category_and_low_priority_remain_silent(self) -> None:
        self.store.add_reminder(
            "Low reminder",
            (self.moment - timedelta(minutes=1)).isoformat(),
            priority="low",
        )
        config = ProactiveConfig(
            enabled=True,
            minimum_priority="normal",
            enabled_categories=("calendar",),
        )

        delivered = self.engine(config=config).run_once()

        self.assertEqual(delivered, ())
        self.assertEqual(self.store.list_notifications(), [])

    def test_system_warnings_are_bounded_to_once_per_day(self) -> None:
        system = SystemSnapshot(
            battery_percent=4,
            plugged_in=False,
            disk_free_percent=2.5,
        )
        engine = self.engine(system=system)

        first = engine.run_once()
        second = engine.run_once()

        self.assertEqual(len(first), 2)
        self.assertTrue(all(item.priority == "critical" for item in first))
        self.assertEqual(second, ())

    def test_equal_quiet_hour_times_disable_quiet_window(self) -> None:
        config = ProactiveConfig(
            enabled=True,
            quiet_hours_start="00:00",
            quiet_hours_end="00:00",
        )

        self.assertFalse(is_quiet_time(self.moment.replace(hour=0), config))


if __name__ == "__main__":
    unittest.main()
