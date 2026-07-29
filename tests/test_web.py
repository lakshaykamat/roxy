import os
import unittest
from unittest.mock import patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DASHBOARD_PASSWORD", "dashboard-password")
os.environ.setdefault("DASHBOARD_SESSION_SECRET", "session-secret")

from fastapi.testclient import TestClient

from src import config
from src.web import _brain_graph, app, render_brain_explorer


class WebTests(unittest.TestCase):
    def setUp(self):
        self.original_password = config.DASHBOARD_PASSWORD
        config.DASHBOARD_PASSWORD = "dashboard-password"

    def tearDown(self):
        config.DASHBOARD_PASSWORD = self.original_password

    def authenticated_client(self):
        client = TestClient(app)
        client.post("/login", content="password=dashboard-password", headers={"content-type": "application/x-www-form-urlencoded"})
        return client

    def test_health_returns_ok(self):
        self.assertEqual(TestClient(app).get("/health").json(), {"status": "ok"})

    def test_dashboard_requires_login(self):
        self.assertEqual(TestClient(app).get("/", follow_redirects=False).status_code, 303)

    def test_brain_page_lists_items_without_persisted_relationship_map(self):
        snapshot = {"items": [{"id": 7, "title": "Focus", "summary": "Focus note", "item_type": "goal", "tags": [], "source_url": None, "source_state": "saved", "captured_at": "2026-07-25T12:00:00+00:00"}]}

        rendered = render_brain_explorer(snapshot)

        self.assertIn("Focus note", rendered)
        self.assertNotIn("KNOWLEDGE_MAP", rendered)
        self.assertNotIn("RELATIONSHIP_LEDGER", rendered)

    def test_brain_graph_links_items_with_shared_tag_or_type(self):
        items = [
            {"id": 1, "title": "Focus", "item_type": "goal", "tags": ["work"]},
            {"id": 2, "title": "Planning", "item_type": "note", "tags": ["work"]},
            {"id": 3, "title": "Exercise", "item_type": "goal", "tags": ["health"]},
        ]

        graph = _brain_graph(items)

        self.assertEqual(graph.count('class="brain-link"'), 2)
        self.assertIn('class="brain-node brain-node--goal"', graph)

    def test_shared_stylesheet_is_available(self):
        response = TestClient(app).get("/assets/styles.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn("--background", response.text)

    def test_brain_data_requires_login(self):
        self.assertEqual(TestClient(app).get("/brain-data", follow_redirects=False).status_code, 303)

    def test_brain_page_passes_search_query_to_shared_retrieval_flow(self):
        snapshot = {"items": []}
        with patch("src.web.load_brain_snapshot", return_value=snapshot) as load_brain_snapshot:
            response = self.authenticated_client().get("/brain?query=SQLite")

        self.assertEqual(response.status_code, 200)
        load_brain_snapshot.assert_called_once_with("SQLite")
        self.assertIn('value="SQLite"', response.text)
