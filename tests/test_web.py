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
from src.web import app, render_brain_explorer, render_dashboard

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

BRAIN_SNAPSHOT = {
    "timeline": [{
        "id": 4,
        "request": "Save project notes",
        "analysis": "Two related project records.",
        "rationale": "Stored separately for recall.",
        "captured_at": "2026-07-25T12:00:00+00:00",
        "items": [{"id": 7, "title": "Focus"}],
    }],
    "items": [{
        "id": 7,
        "title": "Focus",
        "summary": "Focus note",
        "item_type": "goal",
        "tags": ["work"],
        "source_url": "https://example.com/focus",
        "source_state": "analyzed",
        "captured_at": "2026-07-25T12:00:00+00:00",
        "source_published_at": None,
        "capture_summary": "Two related project records.",
        "relations": [{
            "related_item_id": 8,
            "related_item_title": "Plan",
            "relation_type": "supports",
            "explanation": "Focus supports the project plan.",
            "origin": "direct",
            "confidence": 0.9,
        }],
    }],
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

    def test_brain_page_requires_login(self):
        response = TestClient(app).get("/brain", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_brain_data_requires_login(self):
        response = TestClient(app).get("/brain-data", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_brain_data_returns_active_snapshot_for_an_authenticated_session(self):
        client = self.authenticated_client()

        with patch("src.web.dashboard.get_brain_snapshot", return_value=BRAIN_SNAPSHOT):
            response = client.get("/brain-data")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), BRAIN_SNAPSHOT)

    def test_brain_page_renders_timeline_connections_and_accessible_item_details(self):
        client = self.authenticated_client()

        with patch("src.web.dashboard.get_brain_snapshot", return_value=BRAIN_SNAPSHOT):
            response = client.get("/brain")

        self.assertEqual(response.status_code, 200)
        self.assertIn("TIMELINE", response.text)
        self.assertIn("CONNECTIONS", response.text)
        self.assertIn("SAVED_ITEMS", response.text)
        self.assertIn("Focus note", response.text)
        self.assertIn("Focus supports the project plan.", response.text)
        self.assertIn("ORIGIN: direct", response.text)
        self.assertIn("CONFIDENCE: 90%", response.text)
        self.assertIn('datetime="2026-07-25T12:00:00+00:00"', response.text)
        self.assertIn('href="https://example.com/focus"', response.text)
        self.assertIn('data-delete-title="Focus"', response.text)

    def test_brain_page_only_shows_stored_relation_labels(self):
        snapshot = {
            **BRAIN_SNAPSHOT,
            "items": [
                {
                    **BRAIN_SNAPSHOT["items"][0],
                    "tags": ["domain:work"],
                    "captured_at": "2026-07-25T12:00:00+00:00",
                }
            ],
        }

        rendered = render_brain_explorer(snapshot)

        self.assertIn("supports → Plan", rendered)
        self.assertNotIn("DOMAIN:WORK / 2026-07-25", rendered)
        self.assertNotIn("capture date", rendered)

    def test_brain_page_does_not_render_unsafe_source_url_as_a_link(self):
        snapshot = {**BRAIN_SNAPSHOT, "items": [{**BRAIN_SNAPSHOT["items"][0], "source_url": "javascript:alert(1)"}]}
        rendered = render_brain_explorer(snapshot)

        self.assertNotIn('href="javascript:alert(1)"', rendered)
        self.assertIn("javascript:alert(1)", rendered)

    def test_brain_page_returns_unavailable_when_database_fails(self):
        client = self.authenticated_client()

        with patch(
            "src.web.dashboard.get_brain_snapshot",
            side_effect=sqlite3.OperationalError,
        ):
            response = client.get("/brain")

        self.assertEqual(response.status_code, 503)

    def test_brain_data_returns_unavailable_when_database_fails(self):
        client = self.authenticated_client()

        with patch(
            "src.web.dashboard.get_brain_snapshot",
            side_effect=sqlite3.OperationalError,
        ):
            response = client.get("/brain-data")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})

    def test_archive_updates_active_brain_snapshot(self):
        client = self.authenticated_client()

        with patch("src.web.dashboard.archive_brain_item", return_value=True):
            response = client.post("/brain/items/7/archive")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "id": 7, "action": "archived"})

    def test_archive_returns_not_found_for_missing_item(self):
        client = self.authenticated_client()

        with patch("src.web.dashboard.archive_brain_item", return_value=False):
            response = client.post("/brain/items/7/archive")

        self.assertEqual(response.status_code, 404)

    def test_archive_returns_unavailable_when_database_fails(self):
        client = self.authenticated_client()

        with patch(
            "src.web.dashboard.archive_brain_item",
            side_effect=sqlite3.OperationalError,
        ):
            response = client.post("/brain/items/7/archive")

        self.assertEqual(response.status_code, 503)

    def test_delete_requires_named_confirmation(self):
        client = self.authenticated_client()

        response = client.post("/brain/items/7/delete", json={"confirmed": False})

        self.assertEqual(response.status_code, 409)

    def test_delete_requires_the_active_item_title_to_match(self):
        client = self.authenticated_client()

        with patch("src.web.dashboard.delete_brain_item", return_value="mismatch"):
            response = client.post(
                "/brain/items/7/delete",
                json={"confirmed": True, "title": "Different title"},
            )

        self.assertEqual(response.status_code, 409)

    def test_delete_returns_not_found_for_missing_item(self):
        client = self.authenticated_client()

        with patch("src.web.dashboard.delete_brain_item", return_value="not_found"):
            response = client.post(
                "/brain/items/7/delete",
                json={"confirmed": True, "title": "Focus"},
            )

        self.assertEqual(response.status_code, 404)

    def test_delete_removes_an_item_after_named_confirmation(self):
        client = self.authenticated_client()

        with patch("src.web.dashboard.delete_brain_item", return_value="deleted"):
            response = client.post(
                "/brain/items/7/delete",
                json={"confirmed": True, "title": "Focus"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "id": 7, "action": "deleted"})

    def test_dashboard_renders_readable_database_fields_instead_of_raw_data_dictionaries(self):
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

        self.assertIn("<dt>tasks_active</dt>", rendered)
        self.assertIn("<dt>MESSAGE_TOTAL</dt>", rendered)
        self.assertIn('<dt>tasks_active</dt><dd class="font-bold tabular-nums">2</dd>', rendered)
        self.assertIn('<dt>messages_user</dt><dd class="font-bold tabular-nums">3</dd>', rendered)
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
