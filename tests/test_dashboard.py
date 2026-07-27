import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.dashboard import service as dashboard
from src.conversations import history
from src.knowledge import brain
from src.reminders import repository as tasks


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"
        self.now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_snapshot_uses_unified_data_without_service_heartbeats(self):
        history.add("user", "private chat body")
        brain.create_item(
            "private memory", "Private memory", "private memory", "preference", [], "text", "explicit"
        )
        tasks.create_task("Pay rent", "2099-01-01T09:00:00+00:00")

        snapshot = dashboard.get_dashboard_snapshot(self.now)

        self.assertEqual(snapshot["messages"]["total"], 1)
        self.assertEqual(snapshot["memories"]["total"], 1)
        self.assertEqual(snapshot["tasks"]["by_status"]["active"], 1)
        self.assertEqual(snapshot["services"], {})
        self.assertNotIn("private chat body", str(snapshot))
        self.assertNotIn("private memory", str(snapshot))
