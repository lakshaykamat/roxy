import tempfile
import unittest
from pathlib import Path

from src.utils import history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "roxy.db"
        self.original_database_path = history.DATABASE_PATH
        history.DATABASE_PATH = self.database_path

    def tearDown(self):
        history.DATABASE_PATH = self.original_database_path
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
