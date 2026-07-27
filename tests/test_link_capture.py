import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.knowledge.link_capture import capture_public_url


class LinkCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_destination_is_not_requested(self):
        with patch("src.knowledge.link_capture.resolve_public_host", return_value=False), patch(
            "src.knowledge.link_capture.fetch", new=AsyncMock()
        ) as fetch:
            result = await capture_public_url("http://127.0.0.1/private")

        self.assertEqual(result.status, "bookmark")
        fetch.assert_not_awaited()

    async def test_html_source_returns_title_and_visible_text(self):
        response = httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>Roadmap</title><article>Useful text</article>",
        )
        with patch("src.knowledge.link_capture.resolve_public_host", return_value=True), patch(
            "src.knowledge.link_capture.fetch", new=AsyncMock(return_value=response)
        ):
            result = await capture_public_url("https://example.com")

        self.assertEqual((result.title, result.text, result.status), ("Roadmap", "Useful text", "analyzed"))

    async def test_redirect_destination_is_checked_before_fetching(self):
        response = httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        with patch("src.knowledge.link_capture.resolve_public_host", side_effect=[True, False]), patch(
            "src.knowledge.link_capture.fetch", new=AsyncMock(return_value=response)
        ) as fetch:
            result = await capture_public_url("https://example.com")

        self.assertEqual(result.status, "bookmark")
        fetch.assert_awaited_once()
