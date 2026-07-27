import os
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.knowledge import brain_store, tools
from src.knowledge.capture_planner import CaptureItem, CapturePlan, CaptureRelation
from src.reminders import repository as tasks


class BrainStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_brain_items_and_tasks_share_the_current_schema(self):
        brain_store.save_item(
            "My sister Anya lives in Pune.", "Anya", "Anya lives in Pune.",
            "person", [], "text", "explicit",
        )
        task = tasks.create_task("Pay rent", "2099-01-01T09:00:00+00:00")

        item = brain_store.search_items("Anya")[0]
        deliveries = brain_store.list_item_deliveries(task.id)

        self.assertEqual((item.item_type, item.content), ("person", "My sister Anya lives in Pune."))
        self.assertEqual((deliveries[0].brain_item_id, task.id), (task.id, task.id))

    def test_initialize_schema_does_not_read_or_drop_legacy_tables(self):
        connection = sqlite3.connect(config.DATABASE_PATH)
        connection.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT)")
        connection.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL, timezone TEXT NOT NULL, status TEXT NOT NULL, recurrence_rule TEXT, next_due_at TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT)")
        connection.execute("CREATE TABLE reminders (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, scheduled_at TEXT NOT NULL, status TEXT NOT NULL, lease_expires_at TEXT, lease_token TEXT, attempt_count INTEGER NOT NULL, last_error TEXT, delivered_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        timestamp = "2099-01-01T09:00:00+00:00"
        connection.execute("INSERT INTO memories (kind, content, created_at) VALUES ('fact', 'Likes tea', ?)", (timestamp,))
        connection.execute("INSERT INTO tasks (id, title, timezone, status, next_due_at, created_at) VALUES (1, 'Pay rent', 'UTC', 'active', ?, ?)", (timestamp, timestamp))
        connection.execute("INSERT INTO reminders (task_id, scheduled_at, status, attempt_count, created_at, updated_at) VALUES (1, ?, 'pending', 0, ?, ?)", (timestamp, timestamp, timestamp))
        connection.commit()
        connection.close()

        brain_store.initialize_schema()

        with sqlite3.connect(config.DATABASE_PATH) as preserved:
            self.assertEqual(
                preserved.execute("SELECT content FROM memories").fetchone()[0], "Likes tea"
            )
            self.assertEqual(brain_store.list_recent_items(), [])

    def test_repeated_capture_key_creates_one_item(self):
        first = brain_store.save_item("Idea", "Idea", "An idea", "idea", [], "text", "automatic", capture_key="telegram:7:12:12")
        second = brain_store.save_item("Idea", "Idea", "An idea", "idea", [], "text", "automatic", capture_key="telegram:7:12:12")
        self.assertEqual((first.id, second.id, len(brain_store.list_recent_items())), (1, 1, 1))

    def test_compound_capture_keeps_parent_and_two_searchable_children(self):
        plan = CapturePlan(
            "save these", "Two useful insights.", "Separated by topic.",
            [
                CaptureItem("first insight", "First insight", "First insight", "idea", []),
                CaptureItem("second insight", "Second insight", "Second insight", "idea", []),
            ],
        )

        capture = brain_store.save_capture(plan)

        self.assertEqual(len(brain_store.list_capture_items(capture.id)), 2)
        self.assertEqual(brain_store.search_items("first insight")[0].title, "First insight")
        self.assertEqual(len(brain_store.list_capture_timeline(1)), 1)

    def test_relation_is_merged_not_duplicated(self):
        first = brain_store.save_item("First", "First", "First", "idea", [], "text", "explicit")
        second = brain_store.save_item("Second", "Second", "Second", "idea", [], "text", "explicit")

        brain_store.create_relation(first.id, second.id, "supports", "same project evidence", .8, "inferred")
        brain_store.create_relation(first.id, second.id, "supports", "updated evidence", .9, "inferred")

        relations = brain_store.list_item_relations(first.id)
        self.assertEqual(len(relations), 1)
        self.assertEqual((relations[0]["explanation"], relations[0]["confidence"]), ("updated evidence", .9))

    def test_shared_tags_do_not_create_stored_relations(self):
        plan = CapturePlan(
            "save project notes", "Two notes share a tag.", "Stored separately.",
            [
                CaptureItem("First project note", "First", "First note", "project", ["work"]),
                CaptureItem("Second project note", "Second", "Second note", "idea", ["work"]),
            ],
        )

        capture = brain_store.save_capture(plan)
        first, second = brain_store.list_capture_items(capture.id)

        self.assertEqual(brain_store.list_item_relations(first.id), [])
        self.assertEqual(brain_store.list_item_relations(second.id), [])

    def test_capture_creates_explicit_planner_relation(self):
        project = brain_store.save_item(
            "Roxy project", "Roxy", "The Roxy project", "project", [], "text", "explicit",
        )
        plan = CapturePlan(
            "save project source", "A source supports the project.", "Linked with supplied evidence.",
            [CaptureItem(
                "Project roadmap", "Roadmap", "Roadmap source", "reference", [],
                source_url="https://example.com/roadmap",
            )],
            relations=[CaptureRelation(0, project.id, "source_for", "This roadmap documents the Roxy project.", .9)],
        )

        capture = brain_store.save_capture(plan)
        source = brain_store.list_capture_items(capture.id)[0]

        self.assertEqual(
            brain_store.list_item_relations(source.id)[0]["relation_type"], "source_for"
        )

    def test_relation_rejects_invalid_type_explanation_and_confidence(self):
        first = brain_store.save_item("First", "First", "First", "idea", [], "text", "explicit")
        second = brain_store.save_item("Second", "Second", "Second", "idea", [], "text", "explicit")

        for relation_type, explanation, confidence in (
            ("related", "Evidence", .5),
            ("supports", "", .5),
            ("supports", "Evidence", 1.1),
        ):
            with self.assertRaises(ValueError):
                brain_store.create_relation(first.id, second.id, relation_type, explanation, confidence, "planner")

    def test_search_result_includes_capture_provenance_and_relations(self):
        project = brain_store.save_item(
            "Roxy project", "Roxy", "The Roxy project", "project", [], "text", "explicit",
        )
        plan = CapturePlan(
            "save roadmap", "Roadmap analysis", "Source rationale.",
            [CaptureItem(
                "Roxy roadmap", "Roadmap", "Roadmap summary", "reference", [],
                source_url="https://example.com/roadmap", source_published_at="2026-07-01T00:00:00+00:00",
            )],
            relations=[CaptureRelation(0, project.id, "supports", "The roadmap supports the project.", .8)],
        )
        brain_store.save_capture(plan)

        result = asyncio.run(tools.search_saved_items('{"query":"roadmap", "item_type": null}'))
        item = result["brain_items"][0]

        self.assertEqual(item["source_url"], "https://example.com/roadmap")
        self.assertEqual(item["source_published_at"], "2026-07-01T00:00:00+00:00")
        self.assertEqual(item["capture_summary"], "Roadmap analysis")
        self.assertEqual(item["relations"][0]["explanation"], "The roadmap supports the project.")

    def test_automatic_save_respects_the_pause_setting(self):
        brain_store.set_auto_capture_enabled(False)
        result = asyncio.run(
            tools.save_brain_item(
                '{"content":"Idea","title":"Idea","summary":"An idea","item_type":"idea","tags":[],"capture_mode":"automatic"}'
                , capture_key="telegram:7:12:0"
            )
        )

        self.assertFalse(result["ok"])
        self.assertEqual(brain_store.list_recent_items(), [])

    def test_automatic_save_stores_main_model_metadata_without_analysis(self):
        arguments = json.dumps({
            "content": "Lakshay Kamat is an AI engineer.",
            "title": "Lakshay Kamat",
            "summary": "Lakshay works as an AI engineer.",
            "item_type": "fact",
            "tags": ["entity:lakshay kamat", "domain:career"],
            "capture_mode": "automatic",
        })

        with patch("src.knowledge.tools.analyze_and_save_item", new=AsyncMock()) as analyze:
            result = asyncio.run(
                tools.save_brain_item(arguments, capture_key="telegram:7:12:0")
            )

        analyze.assert_not_awaited()
        self.assertTrue(result["ok"])
        item = brain_store.list_recent_items()[0]
        self.assertEqual(
            (item.title, item.summary, item.item_type, item.tags),
            ("Lakshay Kamat", "Lakshay works as an AI engineer.", "fact", ["entity:lakshay kamat", "domain:career"]),
        )

    def test_automatic_save_requires_a_capture_key(self):
        result = asyncio.run(
            tools.save_brain_item(
                '{"content":"Idea","title":"Idea","summary":"An idea","item_type":"idea","tags":[],"capture_mode":"automatic"}'
            )
        )

        self.assertFalse(result["ok"])
        self.assertIn("capture key", result["error"])
        self.assertEqual(brain_store.list_recent_items(), [])

    def test_automatic_save_rejects_a_non_telegram_capture_key(self):
        result = asyncio.run(
            tools.save_brain_item(
                '{"content":"Idea","title":"Idea","summary":"An idea","item_type":"idea","tags":[],"capture_mode":"automatic"}',
                capture_key="not-a-telegram-key",
            )
        )

        self.assertFalse(result["ok"])
        self.assertIn("capture key", result["error"])

    def test_automatic_save_accepts_any_content(self):
        for index, content in enumerate((
            "Don't save this idea",
            "My API key is secret",
            "My home address is 12 Example Street",
        )):
            result = asyncio.run(
                tools.save_brain_item(
                    json.dumps({
                        "content": content, "title": "Private", "summary": content,
                        "item_type": "idea", "tags": [], "capture_mode": "automatic",
                    }),
                    capture_key=f"telegram:7:{index}:0",
                )
            )
            self.assertTrue(result["ok"])
        self.assertEqual(len(brain_store.list_recent_items()), 3)

    def test_brain_tool_searches_archives_and_deletes_items(self):
        saved = asyncio.run(
            tools.save_brain_item(
                '{"content":"Build a freelancer money app","title":"Freelancer app","summary":"A product idea","item_type":"idea","tags":["Product", "product"],"capture_mode":"explicit"}'
            )
        )
        found = asyncio.run(tools.search_saved_items('{"query":"freelancer"}'))
        archived = asyncio.run(
            tools.archive_brain_item(f'{{"id":{saved["brain_item"]["id"]}}}')
        )

        self.assertEqual(found["brain_items"][0]["tags"], ["product"])
        self.assertTrue(archived["ok"])
        self.assertEqual(brain_store.search_items("freelancer"), [])
        self.assertTrue(brain_store.delete_item(saved["brain_item"]["id"]))

    def test_search_tool_definition_requires_a_query_in_strict_mode(self):
        definition = next(
            definition["function"]
            for definition in tools.DEFINITIONS
            if definition["function"]["name"] == "search_saved_items"
        )

        self.assertTrue(definition["strict"])
        self.assertEqual(
            definition["parameters"]["required"], ["query", "item_type"]
        )
        self.assertEqual(
            definition["parameters"]["properties"]["item_type"]["type"],
            ["string", "null"],
        )
        self.assertEqual(
            definition["parameters"]["properties"]["query"]["minLength"], 1
        )
        self.assertEqual(
            definition["parameters"]["properties"]["query"]["pattern"], r".*\S.*"
        )

    def test_export_includes_brain_settings(self):
        brain_store.set_auto_capture_enabled(False)

        self.assertFalse(brain_store.export_brain_data()["brain_settings"]["auto_capture_enabled"])

    def test_graph_data_uses_only_stored_relations(self):
        first = brain_store.save_item(
            "Block afternoons", "Focus block", "Block afternoons", "goal",
            ["focus"], "text", "automatic",
        )
        second = brain_store.save_item(
            "Keep afternoons clear", "No meetings", "Keep afternoons clear",
            "decision", ["focus"], "text", "automatic",
        )
        brain_store.save_item(
            "Finished note", "Finished", "Finished note", "idea",
            ["focus"], "text", "automatic",
        )
        brain_store.archive_item(3)

        graph = brain_store.get_brain_graph()

        self.assertEqual([node["id"] for node in graph["nodes"]], [first.id, second.id])
        self.assertEqual(graph["edges"], [])
        brain_store.create_relation(first.id, second.id, "same domain", "Both records concern focus.", .9, "direct")
        graph = brain_store.get_brain_graph()
        self.assertEqual(graph["edges"][0]["relation_type"], "same domain")

    def test_automatic_save_retries_locked_database_without_duplicates(self):
        arguments = (
            '{"content":"Idea","title":"Idea","summary":"An idea",'
            '"item_type":"idea","tags":[],"capture_mode":"automatic"}'
        )
        original_create_item = brain_store.save_item
        attempts = 0

        def create_item_after_lock(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise sqlite3.OperationalError("database is locked")
            return original_create_item(*args, **kwargs)

        with patch(
            "src.knowledge.tools.brain_store.save_item",
            side_effect=create_item_after_lock,
        ) as mocked_create_item:
            result = asyncio.run(
                tools.save_brain_item(arguments, capture_key="telegram:7:12:12")
            )

        self.assertTrue(result["ok"])
        self.assertEqual(mocked_create_item.call_count, 2)
        self.assertEqual(len(brain_store.list_recent_items()), 1)

    def test_automatic_save_does_not_retry_non_transient_database_errors(self):
        arguments = (
            '{"content":"Idea","title":"Idea","summary":"An idea",'
            '"item_type":"idea","tags":[],"capture_mode":"automatic"}'
        )
        with patch(
            "src.knowledge.tools.brain_store.save_item",
            side_effect=sqlite3.OperationalError("malformed database schema"),
        ) as mocked_create_item:
            result = asyncio.run(tools.save_brain_item(arguments, capture_key="telegram:7:12:12"))

        self.assertFalse(result["ok"])
        self.assertEqual(mocked_create_item.call_count, 1)
