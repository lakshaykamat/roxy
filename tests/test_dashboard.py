import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src import config
from src.dashboard import service as dashboard
from src.knowledge import brain_store


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_brain_snapshot_lists_active_items_without_legacy_metadata(self):
        brain_store.save_item("Keep focus", "Focus", "Keep focus", "goal", [], "text", "explicit")

        snapshot = dashboard.get_brain_snapshot()

        self.assertEqual(snapshot["items"][0]["title"], "Focus")
        self.assertNotIn("timeline", snapshot)
        self.assertNotIn("relations", snapshot["items"][0])

    def test_dashboard_snapshot_uses_scheduled_delivery_data(self):
        brain_store.initialize_schema()
        snapshot = dashboard.get_dashboard_snapshot(datetime.now(timezone.utc))

        self.assertEqual(snapshot["reminders"]["by_status"]["pending"], 0)
