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
from src.knowledge import brain_store
from src.knowledge.capture_planner import CaptureItem, CapturePlan, CaptureRelation
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
        brain_store.save_item(
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

    def test_brain_snapshot_includes_active_items_timeline_and_explained_relations(self):
        project = brain_store.save_item(
            "Roxy project", "Roxy", "The Roxy project", "project", [], "text", "explicit"
        )
        capture = brain_store.save_capture(
            CapturePlan(
                "Save roadmap",
                "The roadmap supports Roxy.",
                "Keep the source with the project.",
                [CaptureItem(
                    "Roadmap details", "Roadmap", "Roadmap details", "reference", [],
                    source_url="https://example.com/roadmap",
                )],
                [CaptureRelation(0, project.id, "source_for", "The roadmap documents Roxy.", .9)],
            )
        )

        snapshot = dashboard.get_brain_snapshot()
        roadmap = next(item for item in snapshot["items"] if item["title"] == "Roadmap")

        self.assertEqual(snapshot["timeline"][0]["id"], capture.id)
        self.assertEqual(roadmap["source_state"], "analyzed")
        self.assertEqual(roadmap["relations"][0]["explanation"], "The roadmap documents Roxy.")

    def test_delete_brain_item_requires_exact_active_title(self):
        item = brain_store.save_item(
            "Keep focus", "Focus", "Keep focus", "goal", [], "text", "explicit"
        )

        self.assertEqual(dashboard.delete_brain_item(item.id, "Other"), "mismatch")
        self.assertEqual(dashboard.delete_brain_item(item.id, "Focus"), "deleted")
        self.assertIsNone(brain_store.get_item(item.id))

    def test_archived_and_deleted_items_are_removed_from_active_relations(self):
        first = brain_store.save_item(
            "Build the roadmap", "Roadmap", "Build the roadmap", "project", [], "text", "explicit"
        )
        second = brain_store.save_item(
            "Write the roadmap", "Draft", "Write the roadmap", "goal", [], "text", "explicit"
        )
        brain_store.create_relation(
            first.id, second.id, "supports", "The draft supports the roadmap.", 0.9, "direct"
        )

        self.assertTrue(dashboard.archive_brain_item(second.id))
        archived_snapshot = dashboard.get_brain_snapshot()
        roadmap = next(item for item in archived_snapshot["items"] if item["id"] == first.id)
        self.assertEqual(roadmap["relations"], [])

        replacement = brain_store.save_item(
            "Draft a launch", "Launch", "Draft a launch", "goal", [], "text", "explicit"
        )
        brain_store.create_relation(
            first.id, replacement.id, "supports", "The launch supports the roadmap.", 0.9, "direct"
        )
        self.assertEqual(dashboard.delete_brain_item(replacement.id, "Launch"), "deleted")
        self.assertFalse(
            any(
                relation["source_item_id"] == replacement.id
                or relation["target_item_id"] == replacement.id
                for relation in brain_store.list_item_relations(first.id)
            )
        )
