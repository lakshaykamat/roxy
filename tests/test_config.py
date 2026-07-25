import os
import unittest
from unittest.mock import patch

from src import config


class ConfigTests(unittest.TestCase):
    def test_validate_configuration_reports_missing_required_values(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "OPENAI_API_KEY": ""}, clear=True):
            self.assertEqual(config.validate_configuration(), ["TELEGRAM_BOT_TOKEN is required.", "OPENAI_API_KEY is required.", "ALLOWED_USER_ID must be a positive integer."])
