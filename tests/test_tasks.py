import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.utils import tasks


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.temporary_directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def create_task(self, recurrence=None):
        return tasks.create_task(
            "Call Dad",
            "2099-01-02T19:00:00+05:30",
            recurrence,
        )

    def test_create_task_uses_default_timezone_and_utc_due_time(self):
        task = self.create_task()

        self.assertEqual(task.timezone, "Asia/Kolkata")
        self.assertEqual(task.next_due_at, datetime(2099, 1, 2, 13, 30, tzinfo=timezone.utc))

    def test_validate_schedule_rejects_naive_past_and_unsupported_values(self):
        with self.assertRaisesRegex(ValueError, "timezone offset"):
            tasks.validate_schedule("2099-01-02T19:00:00", None, None)
        with self.assertRaisesRegex(ValueError, "future"):
            tasks.validate_schedule("2020-01-02T19:00:00+05:30", None, None)
        with self.assertRaisesRegex(ValueError, "Recurrence"):
            tasks.validate_recurrence("yearly")

    def test_next_occurrence_calculates_daily_weekly_and_monthly_schedules(self):
        scheduled_at = datetime(2099, 1, 31, 3, 30, tzinfo=timezone.utc)

        self.assertEqual(
            tasks.next_occurrence(scheduled_at, "Asia/Kolkata", "daily"),
            datetime(2099, 2, 1, 3, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            tasks.next_occurrence(scheduled_at, "Asia/Kolkata", "weekly:monday"),
            datetime(2099, 2, 2, 3, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            tasks.next_occurrence(scheduled_at, "Asia/Kolkata", "monthly:31"),
            datetime(2099, 2, 28, 3, 30, tzinfo=timezone.utc),
        )

    def test_complete_task_hides_it_and_stops_pending_reminders(self):
        task = self.create_task()

        self.assertTrue(tasks.complete_task(task.id))
        self.assertFalse(tasks.complete_task(task.id))
        self.assertEqual(tasks.list_active_tasks(), [])

    def test_claim_recovers_expired_lease_and_delivery_creates_next_recurrence(self):
        task = self.create_task("daily")
        claim_time = task.next_due_at + timedelta(minutes=1)
        first_claim = tasks.claim_due_reminder(claim_time)
        self.assertIsNotNone(first_claim)

        recovered_claim = tasks.claim_due_reminder(claim_time + config.LEASE_DURATION)
        self.assertIsNotNone(recovered_claim)
        self.assertEqual(recovered_claim.id, first_claim.id)

        tasks.mark_reminder_delivered(
            recovered_claim.id, recovered_claim.lease_token, claim_time
        )
        active_task = tasks.list_active_tasks()[0]
        self.assertEqual(active_task.next_due_at, task.next_due_at + timedelta(days=1))

    def test_delivery_completes_one_time_task(self):
        task = self.create_task()
        delivery_time = task.next_due_at + timedelta(minutes=1)
        reminder = tasks.claim_due_reminder(delivery_time)

        tasks.mark_reminder_delivered(reminder.id, reminder.lease_token, delivery_time)

        self.assertEqual(tasks.list_active_tasks(), [])
        with tasks.database_connection() as connection:
            completed_at = connection.execute(
                "SELECT completed_at FROM tasks WHERE id = ?", (task.id,)
            ).fetchone()["completed_at"]
        self.assertEqual(tasks.parse_timestamp(completed_at), delivery_time)

    def test_delivery_skips_missed_recurring_occurrences(self):
        task = self.create_task("daily")
        delivery_time = task.next_due_at + timedelta(days=10)
        reminder = tasks.claim_due_reminder(delivery_time)

        tasks.mark_reminder_delivered(
            reminder.id, reminder.lease_token, delivery_time
        )

        active_task = tasks.list_active_tasks()[0]
        self.assertEqual(active_task.next_due_at, delivery_time + timedelta(days=1))

    def test_delivery_failure_retries_then_becomes_failed(self):
        task = self.create_task()
        claim_time = task.next_due_at + timedelta(minutes=1)
        reminder = tasks.claim_due_reminder(claim_time)
        retry_at = claim_time + timedelta(minutes=2)

        tasks.record_delivery_failure(
            reminder.id, reminder.lease_token, "network down", retry_at
        )
        retried = tasks.claim_due_reminder(retry_at)
        self.assertEqual(retried.attempt_count, 2)
        self.assertEqual(retried.scheduled_at, task.next_due_at)

        for _ in range(config.MAX_DELIVERY_ATTEMPTS - 2):
            tasks.record_delivery_failure(
                retried.id, retried.lease_token, "network down", retry_at
            )
            retried = tasks.claim_due_reminder(retry_at)
        tasks.record_delivery_failure(
            retried.id, retried.lease_token, "network down", retry_at
        )

        with tasks.database_connection() as connection:
            status = connection.execute("SELECT status FROM reminders").fetchone()["status"]
        self.assertEqual(status, "failed")

    def test_claim_uses_configured_lease_duration(self):
        task = self.create_task()
        claim_time = task.next_due_at + timedelta(minutes=1)

        with patch.object(config, "LEASE_DURATION", timedelta(minutes=1)):
            first_claim = tasks.claim_due_reminder(claim_time)
            recovered_claim = tasks.claim_due_reminder(claim_time + timedelta(minutes=1))

        self.assertEqual(recovered_claim.id, first_claim.id)

    def test_delivery_failure_uses_configured_attempt_limit(self):
        task = self.create_task()
        reminder = tasks.claim_due_reminder(task.next_due_at + timedelta(minutes=1))

        with patch.object(config, "MAX_DELIVERY_ATTEMPTS", 1):
            tasks.record_delivery_failure(
                reminder.id, reminder.lease_token, "network down"
            )

        with tasks.database_connection() as connection:
            status = connection.execute("SELECT status FROM reminders").fetchone()["status"]
        self.assertEqual(status, "failed")
