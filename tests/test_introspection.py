import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.knowledge import brain_store
from src.knowledge.brain_analysis import BrainAnalysis, RelationCandidate
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
        with patch(
            "src.knowledge.introspection.ask_brain_analysis",
            new=AsyncMock(return_value=None),
        ):
            await refresh_brain_connections(datetime.now(timezone.utc))
        self.assertEqual(len(brain_store.list_recent_items(100)), before)

    async def test_automatic_save_skips_relation_analysis_until_nightly_refresh(self):
        from src.knowledge import tools

        arguments = (
            '{"content":"Lakshay is an AI engineer","title":"Lakshay",'
            '"summary":"Lakshay works in AI","item_type":"fact",'
            '"tags":["domain:career"],"capture_mode":"automatic"}'
        )
        analysis = BrainAnalysis(
            "Lakshay", "Lakshay works in AI.", "fact", ["domain:career"]
        )
        with patch(
            "src.knowledge.introspection.ask_brain_analysis",
            new=AsyncMock(return_value=analysis),
        ), patch(
            "src.knowledge.introspection.relation_candidates",
            new=AsyncMock(return_value=[]),
        ) as relation_candidates:
            result = await tools.save_brain_item(
                arguments, capture_key="telegram:7:12:0"
            )
            relation_candidates.assert_not_awaited()
            await refresh_brain_connections(datetime.now(timezone.utc))

        self.assertTrue(result["ok"])
        relation_candidates.assert_awaited_once()

    async def test_eligible_items_include_only_active_records_from_the_last_ten_days(self):
        recent = brain_store.save_item("Recent", "Recent", "Recent", "idea", [], "text", "explicit")
        old = brain_store.save_item("Old", "Old", "Old", "idea", [], "text", "explicit")
        archived = brain_store.save_item("Archived", "Archived", "Archived", "idea", [], "text", "explicit")
        now = datetime.now(timezone.utc)
        with brain_store._brain_database() as connection:
            connection.execute(
                "UPDATE brain_items SET created_at = ? WHERE id = ?",
                ((now - timedelta(days=11)).isoformat(), old.id),
            )
            connection.execute("UPDATE brain_items SET status = 'archived' WHERE id = ?", (archived.id,))

        eligible = eligible_for_introspection(now)

        self.assertEqual([item.id for item in eligible], [recent.id])

    async def test_nightly_refresh_translates_content_and_refreshes_relations(self):
        source = brain_store.save_item(
            "रुचि Acme में काम करती है", "Old title", "Old", "idea", [], "text", "explicit"
        )
        target = brain_store.save_item(
            "Acme project", "Acme", "Project", "project", ["entity:acme"], "text", "explicit"
        )
        with brain_store._brain_database() as connection:
            connection.execute(
                "UPDATE brain_items SET created_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(days=11)).isoformat(), target.id),
            )
        analysis = BrainAnalysis(
            "Ruchi at Acme",
            "Ruchi works at Acme.",
            "fact",
            ["entity:acme"],
            content="Ruchi works at Acme.",
        )
        relation = RelationCandidate(
            target.id, "same entity", "Both records refer to Acme.", 1.0, "direct"
        )
        with patch(
            "src.knowledge.introspection.ask_brain_analysis",
            new=AsyncMock(return_value=analysis),
        ), patch(
            "src.knowledge.introspection.relation_candidates",
            new=AsyncMock(return_value=[relation]),
        ):
            refreshed = await refresh_brain_connections(datetime.now(timezone.utc))

        updated = brain_store.get_item(source.id)
        self.assertEqual(refreshed, 1)
        self.assertEqual(updated.content, "Ruchi works at Acme.")
        self.assertEqual(
            (updated.title, updated.summary, updated.tags),
            ("Ruchi at Acme", "Ruchi works at Acme.", ["entity:acme"]),
        )
        self.assertIsNotNone(updated.last_organized_at)
        self.assertEqual(
            brain_store.list_item_relations(source.id)[0]["target_item_id"], target.id
        )

    async def test_nightly_refresh_leaves_record_unchanged_when_analysis_is_unavailable(self):
        item = brain_store.save_item(
            "Exact", "Old title", "Old summary", "idea", [], "text", "explicit"
        )
        with patch(
            "src.knowledge.introspection.ask_brain_analysis",
            new=AsyncMock(return_value=None),
        ):
            refreshed = await refresh_brain_connections(datetime.now(timezone.utc))

        unchanged = brain_store.get_item(item.id)
        self.assertEqual(refreshed, 0)
        self.assertEqual(
            (
                unchanged.content,
                unchanged.title,
                unchanged.summary,
                unchanged.last_organized_at,
            ),
            ("Exact", "Old title", "Old summary", None),
        )

    async def test_nightly_refresh_skips_when_another_organization_is_running(self):
        brain_store.save_item("Exact", "Title", "Summary", "idea", [], "text", "explicit")
        now = datetime.now(timezone.utc)
        lock_token = brain_store.acquire_brain_organization_lock(now)
        self.assertIsNotNone(lock_token)
        with patch(
            "src.knowledge.introspection.ask_brain_analysis",
            new=AsyncMock(),
        ) as analyze:
            refreshed = await refresh_brain_connections(now)

        brain_store.release_brain_organization_lock(lock_token)

        self.assertIsNone(refreshed)
        analyze.assert_not_awaited()

    async def test_nightly_refresh_stops_when_it_loses_the_organization_lock(self):
        brain_store.save_item("First", "First", "First", "idea", [], "text", "explicit")
        brain_store.save_item("Second", "Second", "Second", "idea", [], "text", "explicit")
        with patch(
            "src.knowledge.introspection.brain_store.renew_brain_organization_lock",
            side_effect=[True, False],
        ), patch(
            "src.knowledge.introspection.ask_brain_analysis",
            new=AsyncMock(return_value=None),
        ) as analyze:
            refreshed = await refresh_brain_connections(datetime.now(timezone.utc))

        self.assertEqual(refreshed, 0)
        self.assertEqual(analyze.await_count, 1)

    async def test_nightly_refresh_continues_after_relation_persistence_failure(self):
        first = brain_store.save_item("First", "First", "First", "idea", [], "text", "explicit")
        second = brain_store.save_item("Second", "Second", "Second", "idea", [], "text", "explicit")
        analysis = BrainAnalysis("Organized", "Organized summary", "fact", [])
        relation = RelationCandidate(
            second.id, "same entity", "Both records refer to the same entity.", 1.0, "direct"
        )
        save_relations = MagicMock(side_effect=[OSError("database unavailable"), None])
        with patch(
            "src.knowledge.introspection.ask_brain_analysis",
            new=AsyncMock(return_value=analysis),
        ) as analyze, patch(
            "src.knowledge.introspection.relation_candidates",
            new=AsyncMock(return_value=[relation]),
        ), patch(
            "src.knowledge.introspection.save_relation_candidates", save_relations
        ):
            refreshed = await refresh_brain_connections(datetime.now(timezone.utc))

        self.assertEqual(refreshed, 1)
        self.assertEqual(analyze.await_count, 2)
        self.assertEqual(save_relations.call_count, 2)
        self.assertIsNotNone(brain_store.get_item(first.id).last_organized_at)

    async def test_next_introspection_is_three_am_in_task_timezone(self):
        now = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)
        run_at = next_introspection_at(now, "Asia/Kolkata")
        self.assertEqual(run_at.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M"), "03:00")
