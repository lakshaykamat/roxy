from collections.abc import Iterable

from src.core.errors import try_catch
from src.knowledge import brain_store
from src.knowledge.public_link_reader import normalize_public_url

MAX_RECALL_RESULTS = 5


def search(
    query: str,
    limit: int = MAX_RECALL_RESULTS,
    item_type: str | None = None,
) -> list[brain_store.BrainItem]:
    normalized_query = _normalize_text(query)
    if not normalized_query or limit < 1:
        return []

    exact_matches = [
        item for item in brain_store.list_active_items(limit=None)
        if (item_type is None or item.item_type == item_type)
        and _is_exact_match(item, normalized_query)
    ]
    fts_matches = brain_store.search_items(query, limit=limit, item_type=item_type)
    return _unique_items((*exact_matches, *fts_matches), limit)


def _normalize_text(value: str) -> str:
    return value.strip().casefold()


def _is_exact_match(item: brain_store.BrainItem, query: str) -> bool:
    if _normalize_text(item.title) == query:
        return True
    if any(_normalize_text(tag) == query for tag in item.tags):
        return True
    return item.source_url is not None and _normalized_url(item.source_url) == _normalized_url(query)


def _normalized_url(value: str) -> str:
    return try_catch(
        lambda: normalize_public_url(value).casefold(),
        handle_error=lambda _: _normalize_text(value),
        exception_types=ValueError,
    )


def _unique_items(
    items: Iterable[brain_store.BrainItem], limit: int
) -> list[brain_store.BrainItem]:
    results: list[brain_store.BrainItem] = []
    item_ids: set[int] = set()
    for item in items:
        if item.id in item_ids:
            continue
        item_ids.add(item.id)
        results.append(item)
        if len(results) == limit:
            break
    return results
