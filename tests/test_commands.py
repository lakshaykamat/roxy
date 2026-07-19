import os
import unittest
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

        update.message.reply_text.assert_awaited_once_with("You don't have any active tasks.")

    def test_done_command_rejects_invalid_id(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(args=["bad"])

        import asyncio

        asyncio.run(commands.done(update, context))

        update.message.reply_text.assert_awaited_once_with(
            "Use /done <task id>, for example /done 3."
        )
