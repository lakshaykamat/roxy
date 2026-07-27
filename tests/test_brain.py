import os
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.knowledge import brain, brain_tools
from src.knowledge.captures import CaptureItem, CapturePlan
from src.reminders import repository as tasks


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_migration_moves_memory_task_and_delivery_to_unified_tables(self):
        brain.create_item(
            "My sister Anya lives in Pune.", "Anya", "Anya lives in Pune.",
            "person", [], "text", "explicit",
        )
        task = tasks.create_task("Pay rent", "2099-01-01T09:00:00+00:00")

        item = brain.search_items("Anya")[0]
        deliveries = brain.list_deliveries_for_item(task.id)

        self.assertEqual((item.item_type, item.content), ("person", "My sister Anya lives in Pune."))
        self.assertEqual((deliveries[0].brain_item_id, task.id), (task.id, task.id))

    def test_initialize_schema_migrates_legacy_tables_and_removes_them(self):
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

        brain.initialize_schema()

        exported = brain.export_brain_data()
        self.assertEqual(len(exported["brain_items"]), 2)
        self.assertEqual(len(exported["reminder_deliveries"]), 1)
        with sqlite3.connect(config.DATABASE_PATH) as migrated:
            names = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertFalse({"memories", "tasks", "reminders", "service_heartbeats"} & names)

    def test_repeated_capture_key_creates_one_item(self):
        first = brain.create_item("Idea", "Idea", "An idea", "idea", [], "text", "automatic", capture_key="telegram:7:12:12")
        second = brain.create_item("Idea", "Idea", "An idea", "idea", [], "text", "automatic", capture_key="telegram:7:12:12")
        self.assertEqual((first.id, second.id, len(brain.list_recent_items())), (1, 1, 1))

    def test_compound_capture_keeps_parent_and_two_searchable_children(self):
        plan = CapturePlan(
            "save these", "Two useful insights.", "Separated by topic.",
            [
                CaptureItem("first insight", "First insight", "First insight", "idea", []),
                CaptureItem("second insight", "Second insight", "Second insight", "idea", []),
            ],
        )

        capture = brain.create_capture(plan)

        self.assertEqual(len(brain.items_for_capture(capture.id)), 2)
        self.assertEqual(brain.search_items("first insight")[0].title, "First insight")
        self.assertEqual(len(brain.brain_timeline(1)), 1)

    def test_relation_is_merged_not_duplicated(self):
        first = brain.create_item("First", "First", "First", "idea", [], "text", "explicit")
        second = brain.create_item("Second", "Second", "Second", "idea", [], "text", "explicit")

        brain.create_relation(first.id, second.id, "supports", "same project evidence", .8, "inferred")
        brain.create_relation(first.id, second.id, "supports", "updated evidence", .9, "inferred")

        relations = brain.relations_for_item(first.id)
        self.assertEqual(len(relations), 1)
        self.assertEqual((relations[0]["explanation"], relations[0]["confidence"]), ("updated evidence", .9))

    def test_automatic_save_respects_the_pause_setting(self):
        brain.set_auto_capture_enabled(False)
        result = asyncio.run(
            brain_tools.save_brain_item(
                '{"content":"Idea","title":"Idea","summary":"An idea","item_type":"idea","tags":[],"capture_mode":"automatic"}'
            )
        )

        self.assertFalse(result["ok"])
        self.assertEqual(brain.list_recent_items(), [])

    def test_automatic_save_accepts_any_content(self):
        for content in (
            "Don't save this idea",
            "My API key is secret",
            "My home address is 12 Example Street",
        ):
            result = asyncio.run(
                brain_tools.save_brain_item(
                    json.dumps({
                        "content": content, "title": "Private", "summary": content,
                        "item_type": "idea", "tags": [], "capture_mode": "automatic",
                    })
                )
            )
            self.assertTrue(result["ok"])
        self.assertEqual(len(brain.list_recent_items()), 3)

    def test_brain_tool_searches_archives_and_deletes_items(self):
        saved = asyncio.run(
            brain_tools.save_brain_item(
                '{"content":"Build a freelancer money app","title":"Freelancer app","summary":"A product idea","item_type":"idea","tags":["Product", "product"],"capture_mode":"explicit"}'
            )
        )
        found = asyncio.run(brain_tools.search_brain('{"query":"freelancer"}'))
        archived = asyncio.run(
            brain_tools.archive_brain_item(f'{{"id":{saved["brain_item"]["id"]}}}')
        )

        self.assertEqual(found["brain_items"][0]["tags"], ["product"])
        self.assertTrue(archived["ok"])
        self.assertEqual(brain.search_items("freelancer"), [])
        self.assertTrue(brain.delete_item(saved["brain_item"]["id"]))

    def test_search_tool_definition_requires_a_query_in_strict_mode(self):
        definition = next(
            definition["function"]
            for definition in brain_tools.DEFINITIONS
            if definition["function"]["name"] == "search_brain"
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
        brain.set_auto_capture_enabled(False)

        self.assertFalse(brain.export_brain_data()["brain_settings"]["auto_capture_enabled"])

    def test_graph_data_links_active_items_sharing_a_tag(self):
        first = brain.create_item(
            "Block afternoons", "Focus block", "Block afternoons", "goal",
            ["focus"], "text", "automatic",
        )
        second = brain.create_item(
            "Keep afternoons clear", "No meetings", "Keep afternoons clear",
            "decision", ["focus"], "text", "automatic",
        )
        brain.create_item(
            "Finished note", "Finished", "Finished note", "idea",
            ["focus"], "text", "automatic",
        )
        brain.archive_item(3)

        graph = brain.brain_graph_data()

        self.assertEqual([node["id"] for node in graph["nodes"]], [first.id, second.id])
        self.assertEqual(
            graph["edges"],
            [{"source": first.id, "target": second.id, "tags": ["focus"]}],
        )

    def test_automatic_save_retries_locked_database_without_duplicates(self):
        arguments = (
            '{"content":"Idea","title":"Idea","summary":"An idea",'
            '"item_type":"idea","tags":[],"capture_mode":"automatic"}'
        )
        original_create_item = brain.create_item
        attempts = 0

        def create_item_after_lock(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise sqlite3.OperationalError("database is locked")
            return original_create_item(*args, **kwargs)

        with patch(
            "src.knowledge.brain_tools.brain.create_item",
            side_effect=create_item_after_lock,
        ) as mocked_create_item:
            result = asyncio.run(
                brain_tools.save_brain_item(arguments, capture_key="telegram:7:12:12")
            )

        self.assertTrue(result["ok"])
        self.assertEqual(mocked_create_item.call_count, 2)
        self.assertEqual(len(brain.list_recent_items()), 1)

    def test_automatic_save_does_not_retry_non_transient_database_errors(self):
        arguments = (
            '{"content":"Idea","title":"Idea","summary":"An idea",'
            '"item_type":"idea","tags":[],"capture_mode":"automatic"}'
        )
        with patch(
            "src.knowledge.brain_tools.brain.create_item",
            side_effect=sqlite3.OperationalError("malformed database schema"),
        ) as mocked_create_item:
            result = asyncio.run(brain_tools.save_brain_item(arguments))

        self.assertFalse(result["ok"])
        self.assertEqual(mocked_create_item.call_count, 1)
