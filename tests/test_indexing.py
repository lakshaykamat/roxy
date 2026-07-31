import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.knowledge import brain_store, tools
from src.knowledge.indexing import SourceIndexer
from src.knowledge.public_web_reader import CapturedSource


class SourceIndexerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"
        self.indexer = SourceIndexer()

    async def asyncTearDown(self):
        await self.indexer.stop()
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    async def test_runner_updates_only_automatic_link_placeholders(self):
        item = brain_store.save_item(
            "https://example.com/roadmap",
            "https://example.com/roadmap",
            "Saved link",
            "reference",
            [],
            "capture",
            "explicit",
            source_url="https://example.com/roadmap",
        )
        source = CapturedSource(
            item.source_url,
            "Product roadmap",
            "The planned work for this quarter.",
            None,
            "analyzed",
        )

        with patch("src.knowledge.indexing.read_public_link", new=AsyncMock(return_value=source)):
            await self.indexer.start()
            await asyncio.wait_for(self.indexer._queue.join(), timeout=1)

        enriched = brain_store.get_item(item.id)
        self.assertEqual(enriched.content, "https://example.com/roadmap")
        self.assertEqual(enriched.source_url, "https://example.com/roadmap")
        self.assertEqual(enriched.title, "Product roadmap")
        self.assertEqual(enriched.summary, "The planned work for this quarter.")
        self.assertEqual(enriched.source_status, "ready")

    async def test_runner_marks_unreadable_link_unavailable_without_removing_searchability(self):
        item = brain_store.save_item(
            "https://example.com/broken-link",
            "https://example.com/broken-link",
            "Saved link",
            "reference",
            [],
            "capture",
            "explicit",
            source_url="https://example.com/broken-link",
        )
        source = CapturedSource(item.source_url, None, None, None, "manual_description")

        with patch("src.knowledge.indexing.read_public_link", new=AsyncMock(return_value=source)):
            await self.indexer.start()
            await asyncio.wait_for(self.indexer._queue.join(), timeout=1)

        self.assertEqual(brain_store.get_item(item.id).source_status, "unavailable")
        self.assertEqual([result.id for result in brain_store.search_items("broken-link")], [item.id])

    async def test_runner_keeps_user_provided_title_and_summary(self):
        item = brain_store.save_item(
            "https://example.com/decision",
            "My database decision",
            "Keep this exact summary.",
            "reference",
            [],
            "capture",
            "explicit",
            source_url="https://example.com/decision",
        )
        source = CapturedSource(
            item.source_url,
            "Fetched title",
            "Fetched description.",
            None,
            "analyzed",
        )

        with patch("src.knowledge.indexing.read_public_link", new=AsyncMock(return_value=source)):
            await self.indexer.start()
            await asyncio.wait_for(self.indexer._queue.join(), timeout=1)

        enriched = brain_store.get_item(item.id)
        self.assertEqual(enriched.title, "My database decision")
        self.assertEqual(enriched.summary, "Keep this exact summary.")
        self.assertEqual(enriched.content, "https://example.com/decision")
        self.assertEqual(enriched.source_status, "ready")

    async def test_startup_retries_pending_sources_only(self):
        pending = brain_store.save_item(
            "https://example.com/pending",
            "https://example.com/pending",
            "Saved link",
            "reference",
            [],
            "capture",
            "explicit",
            source_url="https://example.com/pending",
        )
        unavailable = brain_store.save_item(
            "https://example.com/unavailable",
            "https://example.com/unavailable",
            "Saved link",
            "reference",
            [],
            "capture",
            "explicit",
            source_url="https://example.com/unavailable",
        )
        brain_store.mark_source_unavailable(unavailable.id)
        source = CapturedSource(pending.source_url, "Pending link", None, None, "analyzed")

        with patch("src.knowledge.indexing.read_public_link", new=AsyncMock(return_value=source)) as read:
            await self.indexer.start()
            await asyncio.wait_for(self.indexer._queue.join(), timeout=1)

        read.assert_awaited_once_with(pending.source_url)
        self.assertEqual(brain_store.get_item(pending.id).source_status, "ready")
        self.assertEqual(brain_store.get_item(unavailable.id).source_status, "unavailable")

    async def test_explicit_retry_marks_an_unavailable_source_pending_and_enqueues_it(self):
        item = brain_store.save_item(
            "https://example.com/retry",
            "https://example.com/retry",
            "Saved link",
            "reference",
            [],
            "capture",
            "explicit",
            source_url="https://example.com/retry",
        )
        brain_store.mark_source_unavailable(item.id)

        self.assertTrue(await self.indexer.retry(item.id))
        self.assertEqual(brain_store.get_item(item.id).source_status, "pending")
        self.assertEqual(self.indexer._queue.get_nowait(), item.id)


class CaptureEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    async def asyncTearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    async def test_capture_acknowledges_after_save_without_waiting_for_url_fetch(self):
        with patch("src.knowledge.tools.enqueue_source_item") as enqueue:
            result = await tools.capture_brain_content(
                '{"request":"save this link", "urls":["https://example.com/slow"]}'
            )

        self.assertTrue(result["ok"])
        saved_url = next(item for item in brain_store.list_recent_items() if item.source_url)
        self.assertEqual(saved_url.source_status, "pending")
        enqueue.assert_called_once_with(saved_url.id)
