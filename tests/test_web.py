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
from src.web import app, render_brain_explorer


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

    def test_brain_page_lists_items_without_relationship_map(self):
        snapshot = {"items": [{"id": 7, "title": "Focus", "summary": "Focus note", "item_type": "goal", "tags": [], "source_url": None, "source_state": "saved", "captured_at": "2026-07-25T12:00:00+00:00"}]}

        rendered = render_brain_explorer(snapshot)

        self.assertIn("Focus note", rendered)
        self.assertNotIn("KNOWLEDGE_MAP", rendered)
        self.assertNotIn("RELATIONSHIP_LEDGER", rendered)

    def test_brain_data_requires_login(self):
        self.assertEqual(TestClient(app).get("/brain-data", follow_redirects=False).status_code, 303)
