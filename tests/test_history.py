import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.utils import history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "roxy.db"
        self.original_database_path = config.DATABASE_PATH
        config.DATABASE_PATH = self.database_path

    def tearDown(self):
        config.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_get_returns_saved_messages_in_order(self):
        history.add("user", "Hello")
        history.add("assistant", "Hi")

        self.assertEqual(
            history.get(),
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
        )

    def test_get_limits_history_to_forty_messages(self):
        for number in range(41):
            history.add("user", str(number))

        self.assertEqual(
            [message["content"] for message in history.get()],
            [str(number) for number in range(1, 41)],
        )

    def test_get_uses_configured_message_limit(self):
        history.add("user", "first")
        history.add("user", "second")

        with patch.object(config, "MAX_MESSAGES", 1):
            messages = history.get()

        self.assertEqual(messages, [{"role": "user", "content": "second"}])
