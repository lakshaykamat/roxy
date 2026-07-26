import unittest
from pathlib import Path


class DocumentationTests(unittest.TestCase):
    def test_readme_documents_dashboard_port_and_credentials(self):
        readme = Path("README.md").read_text()
        self.assertIn("http://127.0.0.1:8888/", readme)
        self.assertIn("DASHBOARD_PASSWORD", readme)
        self.assertIn("DASHBOARD_SESSION_SECRET", readme)
        self.assertIn("DASHBOARD_SECURE_COOKIES=true", readme)
