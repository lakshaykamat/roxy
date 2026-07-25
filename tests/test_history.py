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

    def test_add_returns_message_id_and_get_before_excludes_boundary(self):
        first_id = history.add("user", "first")
        second_id = history.add("user", "second")

        self.assertEqual(first_id, 1)
        self.assertEqual(second_id, 2)
        self.assertEqual(history.get_before(second_id), [{"role": "user", "content": "first"}])

    def test_get_before_uses_configured_message_limit(self):
        for number in range(3):
            history.add("user", str(number))

        with patch.object(config, "MAX_MESSAGES", 1):
            messages = history.get_before(4)

        self.assertEqual(messages, [{"role": "user", "content": "2"}])

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

    def test_get_excludes_expired_messages(self):
        history.add("user", "expired")
        history.add("user", "current")
        with history.database_connection() as connection:
            connection.execute(
                "UPDATE messages SET expires_at = ? WHERE content = ?",
                ("2000-01-01T00:00:00+00:00", "expired"),
            )

        self.assertEqual(history.get(), [{"role": "user", "content": "current"}])

    def test_get_before_excludes_expired_messages(self):
        history.add("user", "expired")
        current_id = history.add("user", "current")
        with history.database_connection() as connection:
            connection.execute(
                "UPDATE messages SET expires_at = ? WHERE content = ?",
                ("2000-01-01T00:00:00+00:00", "expired"),
            )

        self.assertEqual(history.get_before(current_id), [])

    def test_schema_migration_sets_expiry_for_existing_messages(self):
        import sqlite3

        connection = sqlite3.connect(self.database_path)
        connection.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT NOT NULL, "
            "content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
            ("user", "legacy", "2026-01-01 00:00:00"),
        )
        connection.commit()
        connection.close()

        with history.database_connection() as migrated:
            expires_at = migrated.execute(
                "SELECT expires_at FROM messages WHERE content = ?", ("legacy",)
            ).fetchone()["expires_at"]

        self.assertEqual(expires_at, "2026-04-01T00:00:00+00:00")
