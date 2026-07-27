from dataclasses import dataclass

from src.knowledge.link_capture import CapturedSource


@dataclass(frozen=True)
class CaptureItem:
    content: str
    title: str
    summary: str
    item_type: str
    tags: list[str]
    source_url: str | None = None
    source_published_at: str | None = None


@dataclass(frozen=True)
class CapturePlan:
    request: str
    analysis: str
    rationale: str
    items: list[CaptureItem]


@dataclass(frozen=True)
class Capture:
    id: int
    request: str
    analysis: str
    rationale: str
    captured_at: str


def plan_capture(request: str, sources: list[CapturedSource]) -> CapturePlan:
    clean_request = request.strip()
    items: list[CaptureItem] = []
    for source in sources:
        if source.status == "manual_description":
            continue
        title = source.title or source.url
        text = source.text or title
        items.append(
            CaptureItem(
                content=text,
                title=title,
                summary=(source.text or "Saved link")[:500],
                item_type="reference",
                tags=[],
                source_url=source.url,
                source_published_at=source.published_at,
            )
        )
    if not items:
        items.append(CaptureItem(clean_request, clean_request[:120], clean_request[:500], "idea", []))
    return CapturePlan(
        request=clean_request,
        analysis="Saved the requested information for later recall.",
        rationale="Stored each readable link as a separate reference.",
        items=items,
    )
