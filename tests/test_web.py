import unittest

from fastapi.testclient import TestClient
from unittest.mock import patch

from src.web import app


class WebTests(unittest.TestCase):
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
