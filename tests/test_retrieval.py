import tempfile
import unittest
from pathlib import Path

from src import config
from src.dashboard import service as dashboard
from src.knowledge import brain_store, recall, retrieval


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_search_prioritizes_exact_title_tag_and_normalized_url_matches(self):
        title_item = brain_store.save_item(
            "SQLite works well here.", "Database decision", "Use SQLite.",
            "idea", ["architecture"], "text", "explicit",
        )
        tag_item = brain_store.save_item(
            "Read this later.", "Reading", "Article list.",
            "reference", ["sqlite"], "text", "explicit",
        )
        url_item = brain_store.save_item(
            "https://example.com/guide", "Guide", "Helpful guide.",
            "reference", [], "capture", "explicit",
            source_url="https://example.com/guide",
        )

        self.assertEqual([item.id for item in retrieval.search("database decision")], [title_item.id])
        self.assertEqual([item.id for item in retrieval.search("SQLite")], [tag_item.id, title_item.id])
        self.assertEqual(
            [item.id for item in retrieval.search("https://EXAMPLE.com/guide#section")],
            [url_item.id],
        )

    def test_search_uses_fts_and_excludes_archived_and_deleted_items(self):
        active = brain_store.save_item(
            "The expense tracker needs SQLite FTS.", "Tracker", "FTS plan.",
            "project", [], "text", "explicit",
        )
        archived = brain_store.save_item(
            "The old tracker used SQLite FTS.", "Old tracker", "Old plan.",
            "project", [], "text", "explicit",
        )
        deleted = brain_store.save_item(
            "Deleted tracker details.", "Deleted", "Deleted plan.",
            "project", [], "text", "explicit",
        )
        brain_store.archive_item(archived.id)
        brain_store.delete_item(deleted.id)

        self.assertEqual([item.id for item in retrieval.search("tracker")], [active.id])

    def test_search_returns_at_most_five_unique_items(self):
        for number in range(6):
            brain_store.save_item(
                f"SQLite note {number}", f"Note {number}", "SQLite reference.",
                "idea", [], "text", "explicit",
            )

        items = retrieval.search("SQLite")

        self.assertEqual(len(items), 5)
        self.assertEqual(len({item.id for item in items}), 5)

    def test_recall_returns_grounded_results_and_a_clear_no_result_message(self):
        brain_store.save_item(
            "Use SQLite for Roxy's small single-user app.", "Database decision",
            "Use SQLite for the single-user app.", "idea", [], "text", "explicit",
        )

        self.assertIn("Database decision", recall.reply_for("What did we decide about the database?"))
        self.assertEqual(
            recall.reply_for("What did I save about Kubernetes?"),
            "I couldn't find matching saved information.",
        )
        self.assertIsNone(recall.reply_for("How are you today?"))

    def test_dashboard_search_uses_the_shared_retrieval_function(self):
        matched = brain_store.save_item(
            "My RDBMS learning plan.", "RDBMS plan", "Study databases.",
            "goal", [], "text", "explicit",
        )
        brain_store.save_item(
            "Buy groceries.", "Groceries", "Milk and bread.", "task", [], "text", "explicit",
        )

        snapshot = dashboard.get_brain_snapshot("RDBMS")

        self.assertEqual([item["id"] for item in snapshot["items"]], [matched.id])
