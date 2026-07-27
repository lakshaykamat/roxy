import ipaddress
import logging
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from src import config
from src.core.errors import try_async, try_catch
from src.core.web import fetch_web_response


CaptureStatus = Literal["analyzed", "manual_description", "bookmark"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapturedSource:
    url: str
    title: str | None
    text: str | None
    published_at: str | None
    status: CaptureStatus


class SourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.description: str | None = None
        self.published_at: str | None = None
        self.article_parts: list[str] = []
        self._in_title = False
        self._article_depth = 0
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"article", "main"}:
            self._article_depth += 1
        if tag == "meta" and attributes.get("name", "").lower() == "description":
            self.description = attributes.get("content")
        if tag == "meta" and attributes.get("property", "").lower() == "article:published_time":
            self.published_at = attributes.get("content")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"article", "main"} and self._article_depth:
            self._article_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or self._ignored_depth:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self._article_depth:
            self.article_parts.append(text)

    def extracted(self) -> tuple[str | None, str | None, str | None]:
        title = " ".join(self.title_parts) or None
        text = " ".join(self.article_parts) or self.description
        return title, text, self.published_at


def normalize_public_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Please provide a public http or https link.")
    if parsed.username or parsed.password:
        raise ValueError("Links with credentials are not supported.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global


def resolve_public_host(url: str) -> bool:
    hostname = urlsplit(url).hostname
    if hostname is None:
        return False
    parsed_ip = try_catch(
        lambda: _is_public_ip(hostname),
        handle_error=lambda _: None,
    )
    if parsed_ip is not None:
        return parsed_ip

    def resolve() -> list[tuple[object, ...]]:
        return socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)

    addresses = try_catch(resolve, handle_error=lambda _: [])
    return bool(addresses) and all(_is_public_ip(row[4][0]) for row in addresses)


async def fetch(url: str) -> httpx.Response:
    return await fetch_web_response(
        url,
        timeout_seconds=config.PUBLIC_SOURCE_TIMEOUT_SECONDS,
        max_bytes=config.PUBLIC_SOURCE_MAX_BYTES,
        headers={"User-Agent": "Roxy/1.0"},
    )


def _bookmark(url: str) -> CapturedSource:
    return CapturedSource(url, None, None, None, "bookmark")


async def read_public_link(url: str) -> CapturedSource:
    async def capture() -> CapturedSource:
        current_url = normalize_public_url(url)
        for _ in range(5):
            if not resolve_public_host(current_url):
                return _bookmark(current_url)
            response = await fetch(current_url)
            location = response.headers.get("location")
            if response.is_redirect and location:
                current_url = normalize_public_url(urljoin(current_url, location))
                continue
            if response.status_code >= 400:
                logger.warning("Public link returned HTTP %s: %s", response.status_code, current_url)
                return CapturedSource(current_url, None, None, None, "manual_description")
            if "html" not in response.headers.get("content-type", "").lower():
                logger.warning("Public link did not return HTML: %s", current_url)
                return CapturedSource(current_url, None, None, None, "manual_description")
            parser = SourceParser()
            parser.feed(response.text)
            title, text, published_at = parser.extracted()
            status: CaptureStatus = "analyzed" if title or text else "manual_description"
            return CapturedSource(current_url, title, text, published_at, status)
        logger.warning("Public link exceeded redirect limit: %s", current_url)
        return CapturedSource(current_url, None, None, None, "manual_description")

    async def unavailable(error: BaseException) -> CapturedSource:
        logger.warning("Unable to read public link %s: %s", url, error)
        return CapturedSource(url, None, None, None, "manual_description")

    return await try_async(capture, handle_error=unavailable)
