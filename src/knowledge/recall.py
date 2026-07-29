import re

from src.knowledge import retrieval

RECALL_PATTERNS = (
    re.compile(r"^what did i save(?: about)?\s*(?P<query>.+)?\??$", re.IGNORECASE),
    re.compile(r"^find (?:my )?(?:saved )?(?:note|notes|item|items)(?: about)?\s+(?P<query>.+)$", re.IGNORECASE),
    re.compile(r"^what did we decide about\s+(?P<query>.+)\??$", re.IGNORECASE),
    re.compile(r"^do you remember (?:my )?(?P<query>.+)\??$", re.IGNORECASE),
)


def reply_for(message: str) -> str | None:
    query = _recall_query(message)
    if query is None:
        return None

    items = retrieval.search(query)
    if not items:
        return "I couldn't find matching saved information."

    results = []
    for item in items:
        detail = item.summary or item.content
        source = f" ({item.source_url})" if item.source_url else ""
        results.append(f"{item.title}: {detail}{source}")
    return "Here's what I found in your saved information:\n" + "\n".join(results)


def _recall_query(message: str) -> str | None:
    for pattern in RECALL_PATTERNS:
        match = pattern.match(message.strip())
        if match is None:
            continue
        query = match.group("query").strip(" ?.") if match.group("query") else ""
        return query or None
    return None
