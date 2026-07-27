import asyncio
import json
import logging
import sqlite3

from src.core.errors import retry_async, try_async
from src.knowledge import brain_store
from src.knowledge.brain_analysis import analyze_and_save_capture, analyze_and_save_item
from src.knowledge.capture_planner import build_capture_plan
from src.knowledge.constants import BRAIN_ITEM_TYPES
from src.knowledge.public_link_reader import read_public_link
from src.knowledge.web_research import search_web

logger = logging.getLogger(__name__)
ITEM_TYPES = BRAIN_ITEM_TYPES

DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "capture_brain_content",
            "description": "Immediately save an explicit request and any public links to the user's second brain.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {"type": "string", "minLength": 1},
                    "urls": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["request", "urls"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Research the live web and return cited results. Never saves results.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_brain_item",
            "description": "Save a durable thought to the user's second brain.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"}, "title": {"type": "string"},
                    "summary": {"type": "string"}, "item_type": {"type": "string", "enum": sorted(ITEM_TYPES)},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "capture_mode": {"type": "string", "enum": ["automatic", "explicit"]},
                    "source_url": {"type": "string"},
                },
                "required": ["content", "title", "summary", "item_type", "tags", "capture_mode"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_saved_items", "description": "Search saved brain items.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "pattern": r".*\S.*"},
                    "item_type": {
                        "type": ["string", "null"],
                        "enum": [*sorted(ITEM_TYPES), None],
                    },
                },
                "required": ["query", "item_type"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_brain_item", "description": "Archive one brain item.",
            "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_brain_item", "description": "Permanently delete one brain item.",
            "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"], "additionalProperties": False},
        },
    },
]

WEB_SEARCH_DEFINITION = DEFINITIONS[1]


def _values(arguments: str) -> dict[str, object]:
    values = json.loads(arguments)
    if not isinstance(values, dict):
        raise ValueError("Tool arguments must be an object.")
    return values


def _text(values: dict[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name.replace('_', ' ').capitalize()} is required.")
    return value.strip()


def _item_id(values: dict[str, object]) -> int:
    item_id = values.get("id")
    if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id <= 0:
        raise ValueError("Brain item ID must be a positive whole number.")
    return item_id


def _tags(values: dict[str, object]) -> list[str]:
    raw_tags = values.get("tags")
    if not isinstance(raw_tags, list) or not all(isinstance(tag, str) for tag in raw_tags):
        raise ValueError("Tags must be a list of words.")
    return list(dict.fromkeys(tag.strip().lower() for tag in raw_tags if tag.strip()))


async def save_brain_item(
    arguments: str, *, capture_key: str | None = None
) -> dict[str, object]:
    async def save() -> dict[str, object]:
        values = _values(arguments)
        capture_mode = _text(values, "capture_mode")
        if capture_mode not in {"automatic", "explicit"}:
            raise ValueError("Capture mode must be automatic or explicit.")
        if capture_mode == "automatic" and not await asyncio.to_thread(brain_store.auto_capture_enabled):
            return {"ok": False, "error": "Automatic brain capture is paused."}
        item_type = _text(values, "item_type").lower()
        if item_type not in ITEM_TYPES:
            raise ValueError("Unsupported brain item type.")
        source_url = values.get("source_url")
        if source_url is not None and not isinstance(source_url, str):
            raise ValueError("Source URL must be text.")
        item = await retry_async(
            lambda: analyze_and_save_item(
                _text(values, "content"), item_type, capture_mode,
                title=_text(values, "title"), summary=_text(values, "summary"),
                tags=_tags(values), source_type="text",
                capture_key=capture_key if capture_mode == "automatic" else None,
                source_url=source_url,
            ),
            attempts=3, retry_delay_seconds=0.1, logger=logger,
            error_message="Unable to save brain item", exception_types=sqlite3.OperationalError,
            should_retry=_is_busy_or_locked,
        )
        return {"ok": True, "brain_item": {"id": item.id, "title": item.title, "item_type": item.item_type}}

    async def failure(error: BaseException) -> dict[str, object]:
        logger.exception("Unable to save brain item")
        return {"ok": False, "error": str(error)}

    return await try_async(save, handle_error=failure)


async def capture_brain_content(arguments: str) -> dict[str, object]:
    async def capture() -> dict[str, object]:
        values = _values(arguments)
        request = _text(values, "request")
        raw_urls = values.get("urls")
        if not isinstance(raw_urls, list) or not all(isinstance(url, str) for url in raw_urls):
            raise ValueError("URLs must be a list of links.")
        urls = list(dict.fromkeys(url.strip() for url in raw_urls if url.strip()))
        sources = await asyncio.gather(*(read_public_link(url) for url in urls))
        capture_record = await analyze_and_save_capture(build_capture_plan(request, sources))
        items = await asyncio.to_thread(brain_store.list_capture_items, capture_record.id)
        unreadable = [source for source in sources if source.status == "manual_description"]
        result: dict[str, object] = {
            "ok": True,
            "capture": {"id": capture_record.id, "titles": [item.title for item in items]},
        }
        if unreadable:
            result["needs_description"] = True
            result["question"] = "I couldn't read one of those links. What should I remember about it?"
        return result

    return await try_async(capture, handle_error=_async_failure)


async def search_saved_items(arguments: str) -> dict[str, object]:
    async def search() -> dict[str, object]:
        values = _values(arguments)
        item_type = values.get("item_type")
        if item_type is not None and (not isinstance(item_type, str) or item_type not in ITEM_TYPES):
            raise ValueError("Unsupported brain item type.")
        items = await asyncio.to_thread(brain_store.search_items, _text(values, "query"), 20, item_type)
        results = []
        for item in items:
            context = await asyncio.to_thread(brain_store.get_item_capture_context, item.id)
            results.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "summary": item.summary,
                    "item_type": item.item_type,
                    "tags": item.tags,
                    "source_url": item.source_url,
                    "captured_at": context["captured_at"] or item.created_at.isoformat(),
                    "source_published_at": (
                        item.source_published_at.isoformat() if item.source_published_at else None
                    ),
                    "capture_summary": context["summary"],
                    "relations": context["relations"],
                }
            )
        return {"ok": True, "brain_items": results}

    return await try_async(search, handle_error=_async_failure)


async def archive_brain_item(arguments: str) -> dict[str, object]:
    return await _change_item(arguments, brain_store.archive_item, "archived")


async def delete_brain_item(arguments: str) -> dict[str, object]:
    return await _change_item(arguments, brain_store.delete_item, "deleted")


async def _change_item(arguments: str, operation: object, action: str) -> dict[str, object]:
    async def change() -> dict[str, object]:
        item_id = _item_id(_values(arguments))
        changed = await asyncio.to_thread(operation, item_id)
        return {"ok": changed, "id": item_id, "action": action}

    return await try_async(change, handle_error=_async_failure)


def _failure(error: BaseException) -> dict[str, object]:
    logger.exception("Brain tool failed")
    return {"ok": False, "error": str(error)}


async def _async_failure(error: BaseException) -> dict[str, object]:
    return _failure(error)


def _is_busy_or_locked(error: BaseException) -> bool:
    return "busy" in str(error).lower() or "locked" in str(error).lower()
