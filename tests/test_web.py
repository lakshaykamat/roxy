import unittest

from fastapi.testclient import TestClient

from src.web import app


class WebTests(unittest.TestCase):
    def test_health_returns_ok(self):
        response = TestClient(app).get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
