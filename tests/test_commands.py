import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src.handlers import commands


class CommandTests(unittest.TestCase):
    def test_reset_command_is_not_registered_or_documented(self):
        self.assertNotIn('CommandHandler("reset"', Path("src/app.py").read_text())
        self.assertNotIn("def reset", Path("src/handlers/commands.py").read_text())
        self.assertNotIn("/reset", Path("README.md").read_text())

    def test_tasks_command_shows_empty_state(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("src.handlers.commands.tasks.list_active_tasks", return_value=[]):
            import asyncio

            asyncio.run(commands.list_tasks(update, MagicMock()))

        update.message.reply_text.assert_awaited_once_with(
            "You don't have any active tasks."
        )

    def test_start_command_sends_persistent_tasks_button(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()

        import asyncio

        asyncio.run(commands.start(update, MagicMock()))

        reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(reply_markup.keyboard[0][0].text, commands.TASKS_BUTTON_TEXT)
        self.assertTrue(reply_markup.is_persistent)

    def test_tasks_command_shows_numbered_list_without_completion_buttons(self):
        task = SimpleNamespace(
            id=3,
            title="Pay rent",
            next_due_at=datetime(2026, 7, 21, 9, tzinfo=timezone.utc),
            timezone="UTC",
            recurrence_rule="monthly:21",
        )
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("src.handlers.commands.tasks.list_active_tasks", return_value=[task]):
            import asyncio

            asyncio.run(commands.list_tasks(update, MagicMock()))

        text = update.message.reply_text.await_args.args[0]
        self.assertIn("3. Pay rent", text)
        self.assertIn("(monthly:21)", text)
        self.assertIn("/done <task id>", text)
        self.assertEqual(update.message.reply_text.await_args.kwargs, {})

    def test_completion_callback_completes_task_and_refreshes_list(self):
        update = MagicMock()
        update.callback_query.data = "done:3"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        with (
            patch("src.handlers.commands.tasks.complete_task", return_value=True) as complete_task,
            patch("src.handlers.commands.tasks.list_active_tasks", return_value=[]),
        ):
            import asyncio

            asyncio.run(commands.complete_task_callback(update, MagicMock()))

        complete_task.assert_called_once_with(3)
        update.callback_query.answer.assert_awaited_once_with("Task marked complete.")
        update.callback_query.edit_message_text.assert_awaited_once_with(
            "You don't have any active tasks."
        )

    def test_completion_callback_rejects_malformed_payload(self):
        update = MagicMock()
        update.callback_query.data = "done:not-a-number"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        with patch("src.handlers.commands.tasks.complete_task") as complete_task:
            import asyncio

            asyncio.run(commands.complete_task_callback(update, MagicMock()))

        complete_task.assert_not_called()
        update.callback_query.answer.assert_awaited_once_with("This task action is invalid.")
        update.callback_query.edit_message_text.assert_not_awaited()

    def test_completion_callback_acknowledges_database_failure(self):
        update = MagicMock()
        update.callback_query.data = "done:3"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        with patch(
            "src.handlers.commands.tasks.complete_task", side_effect=RuntimeError("database down")
        ):
            import asyncio

            asyncio.run(commands.complete_task_callback(update, MagicMock()))

        update.callback_query.answer.assert_awaited_once_with(
            "I couldn't update that task. Please try again."
        )
        update.callback_query.edit_message_text.assert_not_awaited()

    def test_completion_callback_refreshes_stale_task(self):
        update = MagicMock()
        update.callback_query.data = "done:3"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        with (
            patch("src.handlers.commands.tasks.complete_task", return_value=False),
            patch("src.handlers.commands.tasks.list_active_tasks", return_value=[]),
        ):
            import asyncio

            asyncio.run(commands.complete_task_callback(update, MagicMock()))

        update.callback_query.answer.assert_awaited_once_with(
            "This task is no longer active."
        )
        update.callback_query.edit_message_text.assert_awaited_once_with(
            "You don't have any active tasks."
        )

    def test_done_command_rejects_invalid_id(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(args=["bad"])

        import asyncio

        asyncio.run(commands.done(update, context))

        update.message.reply_text.assert_awaited_once_with(
            "Use /done <task id>, for example /done 3."
        )
