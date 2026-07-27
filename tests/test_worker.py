import asyncio
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import TimedOut

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src.reminders.repository import Reminder
from src.reminders import worker
from src.reminders.worker import ReminderWorker, retry_delay, startup_retry_delay
from src.prompts.system import SYSTEM_PROMPT


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.reminder = Reminder(
            7, 3, "Take vitamins", datetime.now(timezone.utc), 1, "lease-token"
        )
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        self.worker = ReminderWorker(self.bot)

    async def test_process_next_reminder_marks_successful_delivery(self):
        with patch("src.reminders.worker.repository.claim_due_reminder", return_value=self.reminder), patch(
            "src.reminders.worker.repository.mark_reminder_delivered"
        ) as delivered, patch(
            "src.reminders.worker.ReminderWorker.generate_reminder_message",
            new=AsyncMock(return_value="Good morning, sunshine! ☀️"),
        ):
            processed = await self.worker.process_next_reminder()

        self.assertTrue(processed)
        self.bot.send_message.assert_awaited_once_with(
            chat_id=worker.ALLOWED_USER_ID,
            text="Good morning, sunshine! ☀️",
        )
        delivered.assert_called_once_with(7, "lease-token")

    async def test_generate_reminder_message_falls_back_to_title_when_model_fails(self):
        with patch(
            "src.core.llm.client.chat.completions.create", side_effect=OSError("offline")
        ), self.assertLogs("src.reminders.worker", level="ERROR"):
            message = await self.worker.generate_reminder_message(self.reminder)

        self.assertEqual(message, "Take vitamins")

    async def test_generate_reminder_message_uses_model_response(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Time for your vitamins! 💊"))]
        )
        with patch(
            "src.core.llm.client.chat.completions.create", return_value=response
        ) as create:
            message = await self.worker.generate_reminder_message(self.reminder)

        self.assertEqual(message, "Time for your vitamins! 💊")
        self.assertEqual(create.call_args.kwargs["model"], "gpt-5-mini")
        self.assertIs(create.call_args.kwargs["messages"][0]["content"], SYSTEM_PROMPT)
        self.assertEqual(
            create.call_args.kwargs["messages"][-1]["content"],
            "Write one short, natural Telegram reminder. Return only the message. "
            "Reminder instruction: Take vitamins",
        )

    async def test_process_next_reminder_schedules_retry_for_network_error(self):
        self.bot.send_message.side_effect = OSError("network down")
        with patch("src.reminders.worker.repository.claim_due_reminder", return_value=self.reminder), patch(
            "src.reminders.worker.repository.record_delivery_failure"
        ) as failed, patch(
            "src.reminders.worker.ReminderWorker.generate_reminder_message",
            new=AsyncMock(return_value="Take vitamins"),
        ):
            await self.worker.process_next_reminder()

        self.assertEqual(failed.call_args.args[:3], (7, "lease-token", "network down"))

    async def test_process_next_reminder_returns_false_when_no_reminder_is_due(self):
        with patch("src.reminders.worker.repository.claim_due_reminder", return_value=None):
            processed = await self.worker.process_next_reminder()

        self.assertFalse(processed)
        self.bot.send_message.assert_not_awaited()

    async def test_process_next_reminder_handles_database_error(self):
        with patch("src.reminders.worker.repository.claim_due_reminder", side_effect=sqlite3.OperationalError):
            processed = await self.worker.process_next_reminder()

        self.assertFalse(processed)

    async def test_run_introspection_if_due_runs_once_after_three_am(self):
        at_three_am = datetime(2026, 7, 27, 3, 0, tzinfo=timezone.utc)
        with patch("src.reminders.worker.refresh_brain_connections", new=AsyncMock(return_value=0)) as refresh:
            self.assertTrue(await self.worker.run_introspection_if_due(at_three_am))
            self.assertFalse(await self.worker.run_introspection_if_due(at_three_am))

        refresh.assert_awaited_once_with(at_three_am)

    def test_retry_delay_is_bounded_exponential_backoff(self):
        self.assertEqual(retry_delay(1).total_seconds(), 60)
        self.assertEqual(retry_delay(2).total_seconds(), 120)
        self.assertEqual(retry_delay(10).total_seconds(), 3600)

    def test_startup_retry_delay_is_bounded_exponential_backoff(self):
        self.assertEqual(startup_retry_delay(1), 5)
        self.assertEqual(startup_retry_delay(2), 10)
        self.assertEqual(startup_retry_delay(10), 60)

    async def test_run_worker_retries_telegram_startup_timeout(self):
        failed_bot = MagicMock()
        failed_bot.__aenter__ = AsyncMock(side_effect=TimedOut())
        recovered_bot = MagicMock()
        recovered_bot.__aenter__ = AsyncMock(return_value=recovered_bot)
        recovered_bot.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.reminders.worker.Bot", side_effect=[failed_bot, recovered_bot]
        ) as bot_class, patch(
            "src.reminders.worker.ReminderWorker.run",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ), patch("src.reminders.worker.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await worker.run_worker()

        request = bot_class.call_args.kwargs["request"]
        self.assertEqual(request._client_kwargs["timeout"].connect, 20)
        self.assertEqual(request._client_kwargs["timeout"].read, 20)
        self.assertEqual(request._client_kwargs["timeout"].write, 20)
        self.assertEqual(request._client_kwargs["timeout"].pool, 5)
        sleep.assert_awaited_once_with(5)
