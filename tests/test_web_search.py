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
from src.agent.tool_registry import tool_definitions_for_intent
from src.knowledge import brain, brain_tools
from src.knowledge.web_search import search_web


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    def test_web_search_returns_cited_results_without_persisting(self):
        with patch(
            "src.knowledge.web_search.search",
            new=AsyncMock(return_value=[{"title": "FTS5", "url": "https://sqlite.org/fts5.html", "summary": "Guide"}]),
        ):
            result = asyncio.run(search_web('{"query":"recent SQLite FTS5 guide"}'))

        self.assertTrue(result["ok"])
        self.assertIn("url", result["results"][0])
        self.assertEqual(brain.list_recent_items(), [])

    def test_direct_capture_does_not_require_confirmation(self):
        result = asyncio.run(
            brain_tools.capture_brain_content('{"request":"save this idea", "urls": []}')
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result.get("needs_description", False))
        self.assertEqual(brain.list_recent_items()[0].content, "save this idea")

    def test_web_research_intent_exposes_only_web_search(self):
        self.assertEqual(
            tool_definitions_for_intent("web_research"),
            [brain_tools.WEB_SEARCH_DEFINITION],
        )
