import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src import config
from src.conversations import history
from src.knowledge import brain_store, data_management as privacy
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
        brain_store.save_item("My sister Anya lives in Pune.", "Anya", "Anya lives in Pune.", "person", [], "text", "explicit")
        self.assertEqual(
            [item.content for item in brain_store.search_items("Anya")],
            ["My sister Anya lives in Pune."],
        )

    def test_delete_local_data_removes_messages_and_memories(self):
        history.add("user", "private note")
        brain_store.save_item("Private preference", "Preference", "Private preference", "preference", [], "text", "explicit")
        privacy.delete_user_data()
        self.assertEqual(history.get(), [])
        self.assertEqual(brain_store.list_recent_items(), [])

    def test_export_local_data_is_json_safe(self):
        json.dumps(privacy.export_user_data())

    def test_export_local_data_includes_completed_tasks_and_reminders(self):
        task = tasks.create_task(
            "Pay rent",
            (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        )
        tasks.complete_task(task.id)

        export = privacy.export_user_data()

        self.assertEqual(export["brain_items"][0]["id"], task.id)
        self.assertEqual(export["brain_items"][0]["status"], "completed")
        self.assertEqual(export["scheduled_deliveries"][0]["brain_item_id"], task.id)
