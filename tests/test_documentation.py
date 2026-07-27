import unittest
from pathlib import Path


class DocumentationTests(unittest.TestCase):
    def test_readme_documents_dashboard_port_and_credentials(self):
        readme = Path("README.md").read_text()
        self.assertIn("http://127.0.0.1:8888/", readme)
        self.assertIn("DASHBOARD_PASSWORD", readme)
        self.assertIn("DASHBOARD_SESSION_SECRET", readme)
        self.assertIn("DASHBOARD_SECURE_COOKIES=true", readme)

    def test_readme_documents_brain_privacy_controls(self):
        readme = Path("README.md").read_text()
        self.assertIn("/brain_pause", readme)
        self.assertIn("Tasks are brain items", readme)
        self.assertIn("/brain", readme)
        self.assertIn("share tags", readme)
