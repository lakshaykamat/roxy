import asyncio

from src.config import WEB_SEARCH_MODEL
from src.core.errors import try_async
from src.core.llm import client


def _fields(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return vars(value) if hasattr(value, "__dict__") else {}


def _cited_results(value: object) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    def visit(current: object) -> None:
        if isinstance(current, list):
            for item in current:
                visit(item)
            return
        fields = _fields(current)
        url = fields.get("url")
        if isinstance(url, str) and url:
            title = fields.get("title")
            summary = fields.get("summary") or fields.get("text") or fields.get("content")
            found.append({
                "title": title if isinstance(title, str) and title else url,
                "url": url,
                "summary": summary if isinstance(summary, str) else "",
            })
        for key in ("annotations", "citations", "results", "content", "output"):
            child = fields.get(key)
            if isinstance(child, (list, dict)) or hasattr(child, "__dict__"):
                visit(child)

    visit(value)
    return list({result["url"]: result for result in found}.values())


async def search(query: str) -> list[dict[str, str]]:
    response = await asyncio.to_thread(
        client.responses.create,
        model=WEB_SEARCH_MODEL,
        tools=[{"type": "web_search"}],
        input=query,
    )
    return _cited_results(response)


async def search_web(arguments: str) -> dict[str, object]:
    import json

    async def perform_search() -> dict[str, object]:
        values = json.loads(arguments)
        query = values.get("query") if isinstance(values, dict) else None
        if not isinstance(query, str) or not query.strip():
            raise ValueError("A search query is required.")
        return {"ok": True, "results": await search(query.strip())}

    async def unavailable(_: BaseException) -> dict[str, object]:
        return {"ok": False, "error": "Web research is unavailable right now."}

    return await try_async(perform_search, handle_error=unavailable)
