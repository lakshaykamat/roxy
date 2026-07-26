import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.utils import heartbeats


class HeartbeatsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.temporary_directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_record_heartbeat_updates_timestamp(self):
        first = datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)
        second = datetime(2026, 7, 25, 10, 1, tzinfo=timezone.utc)
        heartbeats.record_heartbeat("bot", first)
        heartbeats.record_heartbeat("bot", second)

        result = heartbeats.get_heartbeats(second)
        self.assertEqual(result["bot"].updated_at, second)
        self.assertEqual(result["bot"].status, "healthy")

    def test_old_heartbeat_is_unhealthy(self):
        now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        heartbeats.record_heartbeat("worker", now - timedelta(seconds=91))
        self.assertEqual(heartbeats.get_heartbeats(now)["worker"].status, "unhealthy")

    def test_unknown_service_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown service name"):
            heartbeats.record_heartbeat("dashboard")
