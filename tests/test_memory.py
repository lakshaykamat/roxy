import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src import config
from src.conversations import history
from src.knowledge import brain, service as privacy
from src.reminders import repository as tasks


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_brain_search_returns_matching_item(self):
        brain.create_item("My sister Anya lives in Pune.", "Anya", "Anya lives in Pune.", "person", [], "text", "explicit")
        self.assertEqual(
            [item.content for item in brain.search_items("Anya")],
            ["My sister Anya lives in Pune."],
        )

    def test_delete_local_data_removes_messages_and_memories(self):
        history.add("user", "private note")
        brain.create_item("Private preference", "Preference", "Private preference", "preference", [], "text", "explicit")
        privacy.delete_local_data()
        self.assertEqual(history.get(), [])
        self.assertEqual(brain.list_recent_items(), [])

    def test_export_local_data_is_json_safe(self):
        json.dumps(privacy.export_local_data())

    def test_export_local_data_includes_completed_tasks_and_reminders(self):
        task = tasks.create_task(
            "Pay rent",
            (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        )
        tasks.complete_task(task.id)

        export = privacy.export_local_data()

        self.assertEqual(export["brain_items"][0]["id"], task.id)
        self.assertEqual(export["brain_items"][0]["status"], "completed")
        self.assertEqual(export["reminder_deliveries"][0]["brain_item_id"], task.id)
