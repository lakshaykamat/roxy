import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DASHBOARD_PASSWORD", "dashboard-password")
os.environ.setdefault("DASHBOARD_SESSION_SECRET", "session-secret")

from fastapi.testclient import TestClient

from src import config
from src.web import app, render_dashboard

SAFE_SNAPSHOT = {
    "generated_at": "2026-07-25T12:00:00+00:00",
    "status": "healthy",
    "services": {"bot": {"status": "healthy", "updated_at": None}, "worker": {"status": "healthy", "updated_at": None}},
    "configuration": {},
    "messages": {"total": 0, "last_24_hours": 0, "by_role": {}, "latest_at": None},
    "memories": {"total": 0, "by_kind": {}, "expiring_within_7_days": 0},
    "tasks": {"by_status": {}},
    "reminders": {"by_status": {}, "overdue_pending": 0, "upcoming": [], "recent_failures": []},
    "notices": [],
}


class WebTests(unittest.TestCase):
    def setUp(self):
        self.original_password = config.DASHBOARD_PASSWORD
        config.DASHBOARD_PASSWORD = "dashboard-password"

    def tearDown(self):
        config.DASHBOARD_PASSWORD = self.original_password

    def authenticated_client(self) -> TestClient:
        client = TestClient(app)
        response = client.post(
            "/login",
            content="password=dashboard-password",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        return client

    def test_health_returns_ok(self):
        response = TestClient(app).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ready_returns_ready_when_database_is_available(self):
        response = TestClient(app).get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    def test_ready_returns_not_ready_when_database_is_unavailable(self):
        with patch("src.web.database_is_available", return_value=False):
            response = TestClient(app).get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})

    def test_dashboard_requires_login(self):
        response = TestClient(app).get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_login_creates_session_and_allows_dashboard(self):
        client = self.authenticated_client()
        with patch("src.web.dashboard.get_dashboard_snapshot", return_value=SAFE_SNAPSHOT):
            self.assertEqual(client.get("/").status_code, 200)

    def test_dashboard_data_returns_snapshot_for_session(self):
        client = self.authenticated_client()
        with patch("src.web.dashboard.get_dashboard_snapshot", return_value=SAFE_SNAPSHOT):
            self.assertEqual(client.get("/dashboard-data").json(), SAFE_SNAPSHOT)

    def test_dashboard_renders_readable_cards_instead_of_raw_data_dictionaries(self):
        snapshot = {
            **SAFE_SNAPSHOT,
            "configuration": {
                "openai_model": "gpt-5-mini",
                "transcription_model": "gpt-4o-mini-transcribe",
                "task_timezone": "Asia/Kolkata",
                "history_retention_days": 90,
                "memory_retention_days": 0,
                "expense_tracker_enabled": True,
            },
            "messages": {"total": 4, "last_24_hours": 2, "by_role": {"user": 3, "assistant": 1}, "latest_at": "2026-07-25T12:00:00+00:00"},
            "memories": {"total": 2, "by_kind": {"fact": 1, "person": 1}, "expiring_within_7_days": 0},
            "tasks": {"by_status": {"active": 2, "completed": 1, "cancelled": 0}},
            "reminders": {"by_status": {"pending": 2, "leased": 0, "delivered": 4, "failed": 0}, "overdue_pending": 0, "upcoming": [], "recent_failures": []},
        }

        rendered = render_dashboard(snapshot)

        self.assertIn("<dt class=\"text-xs font-medium capitalize text-slate-500\">active</dt>", rendered)
        self.assertIn("OpenAI model", rendered)
        self.assertIn("Expense tracking", rendered)
        self.assertNotIn("{'active': 2", rendered)
        self.assertNotIn("'openai_model':", rendered)

    def test_incorrect_credentials_return_unauthorized(self):
        response = TestClient(app).post(
            "/login", content="password=wrong", headers={"content-type": "application/x-www-form-urlencoded"}
        )
        self.assertEqual(response.status_code, 401)

    def test_logout_removes_access(self):
        client = self.authenticated_client()
        self.assertEqual(client.post("/logout", follow_redirects=False).status_code, 303)
        self.assertEqual(client.get("/", follow_redirects=False).status_code, 303)

    def test_database_failure_returns_authenticated_unavailable_json(self):
        client = self.authenticated_client()
        with patch("src.web.dashboard.get_dashboard_snapshot", side_effect=sqlite3.OperationalError):
            response = client.get("/dashboard-data")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
