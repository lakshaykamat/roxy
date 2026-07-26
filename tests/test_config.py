import os
import importlib
import unittest
from unittest.mock import patch

from src import config


class ConfigTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(config)

    def test_validate_configuration_reports_missing_required_values(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "OPENAI_API_KEY": "", "ALLOWED_USER_ID": ""}, clear=True):
            reloaded = importlib.reload(config)
            self.assertEqual(reloaded.validate_configuration(), ["TELEGRAM_BOT_TOKEN is required.", "OPENAI_API_KEY is required.", "ALLOWED_USER_ID must be a positive integer.", "DASHBOARD_PASSWORD is required.", "DASHBOARD_SESSION_SECRET is required."])

    def test_dashboard_configuration_uses_safe_defaults(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-password", "DASHBOARD_SESSION_SECRET": "session-secret"}, clear=False):
            reloaded = importlib.reload(config)
        self.assertEqual(reloaded.HTTP_PORT, 8888)
        self.assertEqual(reloaded.DASHBOARD_SESSION_MAX_AGE_SECONDS, 28800)
        self.assertFalse(reloaded.DASHBOARD_SECURE_COOKIES)
        self.assertEqual(reloaded.HEARTBEAT_INTERVAL_SECONDS, 30)
        self.assertEqual(reloaded.HEARTBEAT_STALE_AFTER_SECONDS, 90)

    def test_validate_configuration_requires_dashboard_credentials(self):
        values = {"TELEGRAM_BOT_TOKEN": "token", "OPENAI_API_KEY": "key", "ALLOWED_USER_ID": "1", "DASHBOARD_PASSWORD": "", "DASHBOARD_SESSION_SECRET": ""}
        with patch.dict(os.environ, values, clear=True):
            reloaded = importlib.reload(config)
        self.assertEqual(reloaded.validate_configuration(), ["DASHBOARD_PASSWORD is required.", "DASHBOARD_SESSION_SECRET is required."])
