import sqlite3
import tempfile
import unittest
from pathlib import Path

from src import config
from src.knowledge import brain_store
from src.reminders import repository as tasks


class BrainStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_initial_schema_contains_only_core_tables(self):
        brain_store.initialize_schema()
        with sqlite3.connect(config.DATABASE_PATH) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertEqual(
            tables,
            {
                "messages",
                "brain_items",
                "scheduled_deliveries",
                "brain_items_fts",
                "brain_items_fts_data",
                "brain_items_fts_idx",
                "brain_items_fts_docsize",
                "brain_items_fts_config",
            },
        )

    def test_schema_initialization_is_idempotent(self):
        brain_store.initialize_schema()
        brain_store.initialize_schema()
        item = brain_store.save_item("Remember this", "Memory", "Remember this", "idea", [], "text", "explicit")

        self.assertEqual(brain_store.get_item(item.id).title, "Memory")

    def test_deleting_item_removes_scheduled_deliveries(self):
        task = tasks.create_task("Pay rent", "2099-01-02T19:00:00+05:30")

        self.assertTrue(brain_store.delete_item(task.id))
        with tasks.database_connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM scheduled_deliveries").fetchone()[0]
        self.assertEqual(count, 0)

    def test_export_uses_scheduled_deliveries_key(self):
        tasks.create_task("Pay rent", "2099-01-02T19:00:00+05:30")

        exported = brain_store.export_brain_data()

        self.assertIn("scheduled_deliveries", exported)
        self.assertNotIn("reminder_deliveries", exported)
