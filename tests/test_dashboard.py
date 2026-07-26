import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.services import dashboard
from src.utils import heartbeats, history, memory, tasks


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.temporary_directory.name) / "roxy.db"
        self.now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        config.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def create_failed_reminders(self, count: int, error: str) -> None:
        with history.database_connection() as connection:
            connection.execute(tasks.CREATE_TASKS_TABLE)
            connection.execute(tasks.CREATE_REMINDERS_TABLE)
            for index in range(count):
                task = connection.execute(
                    "INSERT INTO tasks (title, timezone, status, recurrence_rule, next_due_at, created_at) "
                    "VALUES (?, ?, 'active', NULL, ?, ?)",
                    (f"Reminder {index}", "UTC", self.now.isoformat(), self.now.isoformat()),
                )
                connection.execute(
                    "INSERT INTO reminders (task_id, scheduled_at, status, attempt_count, last_error, created_at, updated_at) "
                    "VALUES (?, ?, 'failed', ?, ?, ?, ?)",
                    (task.lastrowid, self.now.isoformat(), index + 1, error, self.now.isoformat(), self.now.isoformat()),
                )

    def test_snapshot_has_aggregates_not_chat_or_memory_content(self):
        history.add("user", "private chat body")
        history.add("assistant", "private reply")
        memory.create_memory("private memory", "preference")

        snapshot = dashboard.get_dashboard_snapshot(self.now)

        self.assertEqual(snapshot["messages"]["total"], 2)
        self.assertEqual(snapshot["messages"]["by_role"], {"assistant": 1, "user": 1})
        self.assertEqual(snapshot["memories"]["total"], 1)
        self.assertNotIn("private chat body", str(snapshot))
        self.assertNotIn("private memory", str(snapshot))

    def test_snapshot_limits_and_sanitizes_failures(self):
        self.create_failed_reminders(
            6, "Authorization: Bearer top-secret https://api.telegram.org/bot123:token"
        )
        snapshot = dashboard.get_dashboard_snapshot(self.now)

        self.assertEqual(len(snapshot["reminders"]["recent_failures"]), 5)
        self.assertNotIn("top-secret", str(snapshot))
        self.assertNotIn("bot123:token", str(snapshot))
        self.assertNotIn("\n", snapshot["reminders"]["recent_failures"][0]["error"])

    def test_snapshot_does_not_initialize_missing_dashboard_tables(self):
        history.add("user", "private chat body")
        with history.database_connection() as connection:
            tables_before = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }

        dashboard.get_dashboard_snapshot(self.now)

        with history.database_connection() as connection:
            tables_after = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertEqual(tables_after, tables_before)

    def test_snapshot_has_zero_defaults_and_stale_services(self):
        heartbeats.record_heartbeat("bot", self.now)
        heartbeats.record_heartbeat("worker", self.now - timedelta(seconds=91))

        snapshot = dashboard.get_dashboard_snapshot(self.now)

        self.assertEqual(snapshot["tasks"]["by_status"], {"active": 0, "completed": 0, "cancelled": 0})
        self.assertEqual(snapshot["reminders"]["by_status"], {"pending": 0, "leased": 0, "delivered": 0, "failed": 0})
        self.assertEqual(snapshot["services"]["bot"]["status"], "healthy")
        self.assertEqual(snapshot["services"]["worker"]["status"], "unhealthy")
        self.assertEqual(snapshot["status"], "degraded")

    def test_snapshot_lists_five_upcoming_reminders_and_counts_overdue_pending(self):
        with history.database_connection() as connection:
            connection.execute(tasks.CREATE_TASKS_TABLE)
            connection.execute(tasks.CREATE_REMINDERS_TABLE)
            for index in range(6):
                task = connection.execute(
                    "INSERT INTO tasks (title, timezone, status, recurrence_rule, next_due_at, created_at) "
                    "VALUES (?, 'UTC', 'active', ?, ?, ?)",
                    (f"Upcoming {index}", "daily", (self.now + timedelta(hours=index + 1)).isoformat(), self.now.isoformat()),
                )
                connection.execute(
                    "INSERT INTO reminders (task_id, scheduled_at, status, created_at, updated_at) "
                    "VALUES (?, ?, 'pending', ?, ?)",
                    (task.lastrowid, (self.now + timedelta(hours=index + 1)).isoformat(), self.now.isoformat(), self.now.isoformat()),
                )
            overdue_task = connection.execute(
                "INSERT INTO tasks (title, timezone, status, recurrence_rule, next_due_at, created_at) "
                "VALUES ('Overdue', 'UTC', 'active', NULL, ?, ?)",
                ((self.now - timedelta(minutes=1)).isoformat(), self.now.isoformat()),
            )
            connection.execute(
                "INSERT INTO reminders (task_id, scheduled_at, status, created_at, updated_at) "
                "VALUES (?, ?, 'pending', ?, ?)",
                (overdue_task.lastrowid, (self.now - timedelta(minutes=1)).isoformat(), self.now.isoformat(), self.now.isoformat()),
            )

        snapshot = dashboard.get_dashboard_snapshot(self.now)

        self.assertEqual(len(snapshot["reminders"]["upcoming"]), 5)
        self.assertEqual(snapshot["reminders"]["overdue_pending"], 1)
        self.assertEqual(snapshot["reminders"]["upcoming"][0]["recurrence"], "daily")

    def test_snapshot_counts_memories_expiring_within_seven_days(self):
        with history.database_connection() as connection:
            memory._initialize_schema(connection)
            connection.execute(
                "INSERT INTO memories (kind, content, created_at, expires_at) VALUES (?, ?, ?, ?)",
                ("fact", "private", self.now.isoformat(), (self.now + timedelta(days=6)).isoformat()),
            )
            connection.execute(
                "INSERT INTO memories (kind, content, created_at, expires_at) VALUES (?, ?, ?, ?)",
                ("fact", "later", self.now.isoformat(), (self.now + timedelta(days=8)).isoformat()),
            )

        snapshot = dashboard.get_dashboard_snapshot(self.now)

        self.assertEqual(snapshot["memories"]["expiring_within_7_days"], 1)
