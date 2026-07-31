import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.knowledge.public_web_reader import WEB_BROWSER_USER_AGENT, read_public_link


class PublicWebReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_uses_browser_user_agent(self):
        response = httpx.Response(200, text="<html></html>")
        with patch(
            "src.knowledge.public_web_reader.fetch_web_response",
            new=AsyncMock(return_value=response),
        ) as fetch_web_response:
            from src.knowledge.public_web_reader import fetch

            await fetch("https://example.com")

        self.assertEqual(
            fetch_web_response.await_args.kwargs["headers"],
            {"User-Agent": WEB_BROWSER_USER_AGENT},
        )

    async def test_fetch_removes_encoding_headers_after_decoding_body(self):
        class FakeResponse:
            status_code = 200
            headers = {
                "content-type": "text/html",
                "content-encoding": "gzip",
                "content-length": "32",
            }
            request = httpx.Request("GET", "https://example.com")

            async def aiter_bytes(self):
                yield b"<title>Roadmap</title><article>Useful text</article>"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_):
                return None

        class FakeClient:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            def stream(self, *_args, **_kwargs):
                return FakeStream()

        with patch("src.core.web.httpx.AsyncClient", FakeClient):
            from src.knowledge.public_web_reader import fetch

            result = await fetch("https://example.com")

        self.assertNotIn("content-encoding", result.headers)
        self.assertNotEqual(result.headers["content-length"], "32")
        self.assertEqual(result.text, "<title>Roadmap</title><article>Useful text</article>")

    async def test_private_destination_is_not_requested(self):
        with patch("src.knowledge.public_web_reader.resolve_public_host", return_value=False), patch(
            "src.knowledge.public_web_reader.fetch", new=AsyncMock()
        ) as fetch:
            result = await read_public_link("http://127.0.0.1/private")

        self.assertEqual(result.status, "bookmark")
        fetch.assert_not_awaited()

    async def test_html_source_returns_title_and_visible_text(self):
        response = httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><head><title>Roadmap</title></head><body>"
                "Intro<nav>Navigation</nav><article>Useful text</article>"
                "<footer>Contact</footer><script>hidden()</script>"
                "<style>.hidden {}</style><noscript>hidden fallback</noscript>"
                "</body></html>"
            ),
        )
        with patch("src.knowledge.public_web_reader.resolve_public_host", return_value=True), patch(
            "src.knowledge.public_web_reader.fetch", new=AsyncMock(return_value=response)
        ):
            result = await read_public_link("https://example.com")

        self.assertEqual(
            (result.title, result.text, result.status),
            ("Roadmap", "Intro Navigation Useful text Contact", "analyzed"),
        )

    async def test_successful_crawl_logs_start_and_completion_metadata(self):
        response = httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Roadmap</title></head><body>Useful text</body></html>",
        )
        with patch("src.knowledge.public_web_reader.resolve_public_host", return_value=True), patch(
            "src.knowledge.public_web_reader.fetch", new=AsyncMock(return_value=response)
        ), self.assertLogs("src.knowledge.public_web_reader", level="INFO") as logs:
            await read_public_link("https://example.com")

        output = "\n".join(logs.output)
        self.assertIn("Public link crawl started: https://example.com/", output)
        self.assertIn(
            "Public link crawl completed: url=https://example.com/ http_status=200 "
            "status=analyzed text_length=11",
            output,
        )

    async def test_http_failure_is_logged_before_requesting_a_description(self):
        response = httpx.Response(404, headers={"content-type": "text/html"})
        with patch("src.knowledge.public_web_reader.resolve_public_host", return_value=True), patch(
            "src.knowledge.public_web_reader.fetch", new=AsyncMock(return_value=response)
        ), self.assertLogs("src.knowledge.public_web_reader", level="WARNING") as logs:
            result = await read_public_link("https://example.com/missing")

        self.assertEqual(result.status, "manual_description")
        self.assertIn("HTTP 404", logs.output[0])

    async def test_redirect_destination_is_checked_before_fetching(self):
        response = httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        with patch("src.knowledge.public_web_reader.resolve_public_host", side_effect=[True, False]), patch(
            "src.knowledge.public_web_reader.fetch", new=AsyncMock(return_value=response)
        ) as fetch:
            result = await read_public_link("https://example.com")

        self.assertEqual(result.status, "bookmark")
        fetch.assert_awaited_once()
