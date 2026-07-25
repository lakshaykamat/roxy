import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src import config
from src.utils import history, memory, tasks


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_find_relevant_memories_returns_matching_memory(self):
        memory.create_memory("My sister Anya lives in Pune.", kind="person")
        self.assertEqual([item.content for item in memory.find_relevant_memories("How is Anya doing?")], ["My sister Anya lives in Pune."])

    def test_delete_local_data_removes_messages_and_memories(self):
        history.add("user", "private note")
        memory.create_memory("Private preference")
        memory.delete_local_data()
        self.assertEqual(history.get(), [])
        self.assertEqual(memory.list_memories(), [])

    def test_export_local_data_is_json_safe(self):
        json.dumps(memory.export_local_data())

    def test_export_local_data_includes_completed_tasks_and_reminders(self):
        task = tasks.create_task(
            "Pay rent",
            (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        )
        tasks.complete_task(task.id)

        export = memory.export_local_data()

        self.assertEqual(export["tasks"][0]["id"], task.id)
        self.assertEqual(export["tasks"][0]["status"], "completed")
        self.assertEqual(export["reminders"][0]["task_id"], task.id)
