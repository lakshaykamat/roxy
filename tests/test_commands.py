import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src.handlers import commands
from src import config
from src.knowledge import brain_store


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_reset_command_is_not_registered_or_documented(self):
        self.assertNotIn('CommandHandler("reset"', Path("src/app.py").read_text())
        self.assertNotIn("def reset", Path("src/handlers/commands.py").read_text())
        self.assertNotIn("/reset", Path("README.md").read_text())

    def test_legacy_memory_commands_are_not_registered(self):
        self.assertNotIn('CommandHandler("memory"', Path("src/app.py").read_text())
        self.assertNotIn('CommandHandler("forget"', Path("src/app.py").read_text())

    def test_tasks_command_shows_empty_state(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("src.handlers.commands.tasks.list_active_tasks", return_value=[]):
            import asyncio

            asyncio.run(commands.list_tasks(update, MagicMock()))

        update.message.reply_text.assert_awaited_once_with(
            "You don't have any active tasks.", reply_markup=None
        )

    def test_start_command_sends_all_persistent_buttons(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()

        import asyncio

        asyncio.run(commands.start(update, MagicMock()))

        reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        button_texts = [button.text for row in reply_markup.keyboard for button in row]
        self.assertEqual(
            button_texts,
            [
                commands.TASKS_BUTTON_TEXT,
                commands.BRAIN_BUTTON_TEXT,
                commands.PAUSE_BRAIN_BUTTON_TEXT,
                commands.RESUME_BRAIN_BUTTON_TEXT,
                commands.EXPORT_DATA_BUTTON_TEXT,
                commands.DELETE_DATA_BUTTON_TEXT,
                commands.HELP_BUTTON_TEXT,
            ],
        )
        self.assertTrue(reply_markup.is_persistent)

    def test_tasks_button_shows_numbered_list_with_completion_buttons(self):
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
        self.assertIn("Use the Done buttons below", text)
        reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(reply_markup.inline_keyboard[0][0].callback_data, "done:3")

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
            "You don't have any active tasks.", reply_markup=None
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
            "You don't have any active tasks.", reply_markup=None
        )

    def test_brain_pause_disables_capture(self):
        self.assertEqual(commands.brain_pause_response(), "Automatic brain capture is paused.")
        self.assertFalse(brain_store.auto_capture_enabled())
        self.assertEqual(commands.brain_resume_response(), "Automatic brain capture is on.")
        self.assertTrue(brain_store.auto_capture_enabled())

    def test_brain_button_lists_items(self):
        item = brain_store.save_item("Focus afternoons", "Focus block", "Keep afternoons clear", "goal", ["focus"], "text", "explicit")

        self.assertIn("Focus block [goal]", commands.brain_list_response())

    def test_delete_button_requires_keyboard_confirmation(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(user_data={})

        import asyncio

        asyncio.run(commands.request_data_deletion(update, context))

        reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            [button.text for button in reply_markup.keyboard[0]],
            [commands.CONFIRM_DELETE_BUTTON_TEXT, commands.CANCEL_DELETE_BUTTON_TEXT],
        )
        self.assertTrue(context.user_data["confirming_data_deletion"])

    def test_delete_confirmation_requires_a_previous_request(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(user_data={})

        import asyncio

        with patch("src.handlers.commands.privacy.delete_user_data") as delete_user_data:
            asyncio.run(commands.confirm_data_deletion(update, context))

        delete_user_data.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(
            "Choose Delete my data first.", reply_markup=commands.main_keyboard()
        )
