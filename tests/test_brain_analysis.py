import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.knowledge import brain_store
from src.knowledge.brain_analysis import BrainAnalysis, RelationCandidate, analyze_and_save_item


class BrainAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.directory.name) / "roxy.db"

    def tearDown(self):
        config.DATABASE_PATH = self.original_path
        self.directory.cleanup()

    async def test_shared_entity_creates_a_direct_relation(self):
        analyses = [
            BrainAnalysis("Ruchi", "Ruchi is my girlfriend.", "person", ["entity:ruchi"]),
            BrainAnalysis("Ruchi's birthday", "Ruchi's birthday is 17 June.", "fact", ["entity:ruchi"]),
        ]
        with patch("src.knowledge.brain_analysis.ask_brain_analysis", new=AsyncMock(side_effect=analyses)):
            await analyze_and_save_item("My girlfriend is Ruchi.", "idea", "explicit")
            second = await analyze_and_save_item("Ruchi's birthday is 17 June 2005.", "idea", "explicit")

        relation = brain_store.list_item_relations(second.id)[0]
        self.assertEqual((relation["relation_type"], relation["origin"]), ("same entity", "direct"))
        self.assertEqual(relation["explanation"], "Both records refer to Ruchi.")

    async def test_analysis_failure_keeps_a_minimal_unconnected_record(self):
        with patch("src.knowledge.brain_analysis.ask_brain_analysis", new=AsyncMock(return_value=None)):
            saved = await analyze_and_save_item("Ruchi's birthday is 17 June 2005.", "idea", "explicit")

        self.assertEqual((saved.title, saved.tags), ("Ruchi's birthday is 17 June 2005.", []))
        self.assertEqual(brain_store.list_item_relations(saved.id), [])

    async def test_inferred_relation_requires_the_strict_confidence_threshold(self):
        brain_store.save_item("Prepare for interview", "Interview", "Prepare", "goal", ["domain:career"], "text", "explicit")
        analysis = BrainAnalysis("Practice answers", "Practice concise interview answers.", "idea", [])
        weak_relation = RelationCandidate(1, "related topic (inferred)", "Both concern interview preparation.", .74, "inferred")
        with patch("src.knowledge.brain_analysis.ask_brain_analysis", new=AsyncMock(return_value=analysis)), patch(
            "src.knowledge.brain_analysis.ask_relation_candidates", new=AsyncMock(return_value=[weak_relation])
        ):
            saved = await analyze_and_save_item("Practice concise interview answers", "idea", "explicit")

        self.assertEqual(brain_store.list_item_relations(saved.id), [])
