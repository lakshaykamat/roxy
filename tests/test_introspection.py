import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.knowledge import brain_store
from src.knowledge.introspection import eligible_for_introspection, next_introspection_at, refresh_brain_connections


class IntrospectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    async def test_nightly_refresh_never_creates_a_synthetic_item(self):
        brain_store.save_item("Recent thought", "Recent", "Recent", "idea", ["domain:work"], "text", "explicit")
        before = len(brain_store.list_recent_items(100))
        await refresh_brain_connections(datetime.now(timezone.utc))
        self.assertEqual(len(brain_store.list_recent_items(100)), before)

    async def test_eligible_items_include_recent_and_unconnected_records(self):
        recent = brain_store.save_item("Recent", "Recent", "Recent", "idea", [], "text", "explicit")
        old = brain_store.save_item("Old", "Old", "Old", "idea", [], "text", "explicit")
        with brain_store._brain_database() as connection:
            connection.execute(
                "UPDATE brain_items SET created_at = ?, updated_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(), (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(), old.id),
            )
        self.assertEqual({item.id for item in eligible_for_introspection(datetime.now(timezone.utc))}, {recent.id, old.id})

    async def test_next_introspection_is_three_am_in_task_timezone(self):
        now = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)
        run_at = next_introspection_at(now, "Asia/Kolkata")
        self.assertEqual(run_at.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M"), "03:00")
