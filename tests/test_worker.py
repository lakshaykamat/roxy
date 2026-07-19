import os
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src.utils.tasks import Reminder
from src.worker import ReminderWorker, retry_delay


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.reminder = Reminder(
            7, 3, "Take vitamins", datetime.now(timezone.utc), 1, "lease-token"
        )
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        self.worker = ReminderWorker(self.bot)

    async def test_process_next_reminder_marks_successful_delivery(self):
        with patch("src.worker.tasks.claim_due_reminder", return_value=self.reminder), patch(
            "src.worker.tasks.mark_reminder_delivered"
        ) as delivered:
            processed = await self.worker.process_next_reminder()

        self.assertTrue(processed)
        self.bot.send_message.assert_awaited_once_with(chat_id=1, text="Reminder: Take vitamins")
        delivered.assert_called_once_with(7, "lease-token")

    async def test_process_next_reminder_schedules_retry_for_network_error(self):
        self.bot.send_message.side_effect = OSError("network down")
        with patch("src.worker.tasks.claim_due_reminder", return_value=self.reminder), patch(
            "src.worker.tasks.record_delivery_failure"
        ) as failed:
            await self.worker.process_next_reminder()

        self.assertEqual(failed.call_args.args[:3], (7, "lease-token", "network down"))

    async def test_process_next_reminder_returns_false_when_no_reminder_is_due(self):
        with patch("src.worker.tasks.claim_due_reminder", return_value=None):
            processed = await self.worker.process_next_reminder()

        self.assertFalse(processed)
        self.bot.send_message.assert_not_awaited()

    async def test_process_next_reminder_handles_database_error(self):
        with patch("src.worker.tasks.claim_due_reminder", side_effect=sqlite3.OperationalError):
            processed = await self.worker.process_next_reminder()

        self.assertFalse(processed)

    def test_retry_delay_is_bounded_exponential_backoff(self):
        self.assertEqual(retry_delay(1).total_seconds(), 60)
        self.assertEqual(retry_delay(2).total_seconds(), 120)
        self.assertEqual(retry_delay(10).total_seconds(), 3600)
