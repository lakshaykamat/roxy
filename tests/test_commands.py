import unittest
from pathlib import Path


class CommandTests(unittest.TestCase):
    def test_reset_command_is_not_registered_or_documented(self):
        self.assertNotIn('CommandHandler("reset"', Path("src/app.py").read_text())
        self.assertNotIn("def reset", Path("src/handlers/commands.py").read_text())
        self.assertNotIn("/reset", Path("README.md").read_text())
