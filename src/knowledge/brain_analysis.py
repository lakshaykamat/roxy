import asyncio
import json
from dataclasses import dataclass, replace

from src.core.errors import try_async
from src.core.llm import client
from src.config import OPENAI_MODEL
from src.knowledge import brain_store
from src.knowledge.capture_planner import CaptureItem, CapturePlan
from src.knowledge.constants import (
    BRAIN_ITEM_TYPES,
    RELATION_CONFIDENCE_THRESHOLD,
    ConnectionRelationType,
)


@dataclass(frozen=True)
class BrainAnalysis:
    title: str
    summary: str
    item_type: str
    tags: list[str]


@dataclass(frozen=True)
class RelationCandidate:
    target_item_id: int
    relation_type: ConnectionRelationType
    explanation: str
    confidence: float
    origin: str


def normalize_tag(kind: str, value: str) -> str | None:
    normalized = " ".join(value.lower().split())
    if not normalized or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789 -" for character in normalized):
        return None
    return f"{kind}:{normalized}"


def _analysis_schema() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "brain_analysis",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"}, "summary": {"type": "string"},
                    "item_type": {"type": "string", "enum": sorted(BRAIN_ITEM_TYPES)},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "domains": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "summary", "item_type", "entities", "domains"],
                "additionalProperties": False,
            },
        },
    }


def _analysis_from_content(content: str, item_type: str, response: str) -> BrainAnalysis | None:
    values = json.loads(response)
    title = values.get("title")
    summary = values.get("summary")
    analyzed_type = values.get("item_type")
    entities = values.get("entities")
    domains = values.get("domains")
    if not all(isinstance(value, str) and value.strip() for value in (title, summary, analyzed_type)):
        return None
    if analyzed_type not in BRAIN_ITEM_TYPES:
        return None
    if not isinstance(entities, list) or not isinstance(domains, list):
        return None
    tags = [
        tag for kind, values in (("entity", entities), ("domain", domains))
        for value in values if isinstance(value, str)
        for tag in [normalize_tag(kind, value)] if tag is not None
    ]
    tags = list(dict.fromkeys(tags))
    if len(tags) > 12:
        return None
    return BrainAnalysis(title.strip(), summary.strip(), analyzed_type, tags)


async def ask_brain_analysis(content: str, item_type: str) -> BrainAnalysis | None:
    async def analyze() -> BrainAnalysis | None:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=OPENAI_MODEL,
            response_format=_analysis_schema(),
            messages=[
                {"role": "system", "content": "Return concise user-visible metadata for a saved thought. Do not include reasoning or hidden scratch work."},
                {"role": "user", "content": f"Thought: {content}\nSuggested type: {item_type}"},
            ],
        )
        return _analysis_from_content(content, item_type, response.choices[0].message.content or "{}")

    async def unavailable(_: BaseException) -> BrainAnalysis | None:
        return None

    return await try_async(analyze, handle_error=unavailable)


async def analyze_brain_item(content: str, item_type: str) -> BrainAnalysis | None:
    return await ask_brain_analysis(content, item_type)


def direct_relation_candidates(item: brain_store.BrainItem) -> list[RelationCandidate]:
    candidates: list[RelationCandidate] = []
    item_tags = set(item.tags)
    for existing in brain_store.list_active_items(100):
        if existing.id == item.id:
            continue
        shared_entities = sorted(tag for tag in item_tags & set(existing.tags) if tag.startswith("entity:"))
        shared_domains = sorted(tag for tag in item_tags & set(existing.tags) if tag.startswith("domain:"))
        if shared_entities:
            name = shared_entities[0].split(":", 1)[1].title()
            candidates.append(RelationCandidate(existing.id, "same entity", f"Both records refer to {name}.", 1.0, "direct"))
        elif shared_domains:
            domain = shared_domains[0].split(":", 1)[1]
            candidates.append(RelationCandidate(existing.id, "same domain", f"Both records concern {domain}.", 0.9, "direct"))
    return candidates


def _relation_schema(candidate_ids: list[int]) -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "brain_relations",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "target_item_id": {"type": "integer", "enum": candidate_ids},
                                "explanation": {"type": "string"}, "confidence": {"type": "number"},
                            },
                            "required": ["target_item_id", "explanation", "confidence"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["relations"], "additionalProperties": False,
            },
        },
    }


async def ask_relation_candidates(item: brain_store.BrainItem, candidates: list[brain_store.BrainItem]) -> list[RelationCandidate]:
    candidate_ids = [candidate.id for candidate in candidates]
    if not candidate_ids:
        return []

    async def analyze() -> list[RelationCandidate]:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=OPENAI_MODEL,
            response_format=_relation_schema(candidate_ids),
            messages=[
                {"role": "system", "content": "Identify only clearly related topics. Return concise user-visible explanations, never reasoning."},
                {"role": "user", "content": json.dumps({
                    "new_item": {"title": item.title, "summary": item.summary, "tags": item.tags},
                    "candidates": [{"id": candidate.id, "title": candidate.title, "summary": candidate.summary, "tags": candidate.tags} for candidate in candidates],
                })},
            ],
        )
        values = json.loads(response.choices[0].message.content or "{}")
        relations = values.get("relations")
        if not isinstance(relations, list):
            return []
        return [
            RelationCandidate(value["target_item_id"], "related topic (inferred)", value["explanation"].strip(), value["confidence"], "inferred")
            for value in relations
            if isinstance(value, dict)
            and value.get("target_item_id") in candidate_ids
            and isinstance(value.get("explanation"), str) and value["explanation"].strip()
            and isinstance(value.get("confidence"), (int, float)) and not isinstance(value.get("confidence"), bool)
            and value["confidence"] >= RELATION_CONFIDENCE_THRESHOLD
        ]

    async def unavailable(_: BaseException) -> list[RelationCandidate]:
        return []

    return await try_async(analyze, handle_error=unavailable)


async def relation_candidates(item: brain_store.BrainItem) -> list[RelationCandidate]:
    direct = direct_relation_candidates(item)
    if direct:
        return direct
    existing = [candidate for candidate in brain_store.list_active_items(20) if candidate.id != item.id]
    return await ask_relation_candidates(item, existing)


def save_relation_candidates(item_id: int, candidates: list[RelationCandidate]) -> None:
    for candidate in candidates:
        if candidate.confidence < RELATION_CONFIDENCE_THRESHOLD and candidate.origin == "inferred":
            continue
        source_id, target_id = sorted((item_id, candidate.target_item_id))
        brain_store.create_relation(source_id, target_id, candidate.relation_type, candidate.explanation, candidate.confidence, candidate.origin)


async def analyze_and_save_item(
    content: str,
    item_type: str,
    capture_mode: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    source_type: str = "text",
    capture_key: str | None = None,
    source_url: str | None = None,
) -> brain_store.BrainItem:
    analysis = await analyze_brain_item(content, item_type)
    saved = await asyncio.to_thread(
        brain_store.save_item,
        content,
        analysis.title if analysis else (title or content[:120]),
        analysis.summary if analysis else (summary or content[:500]),
        analysis.item_type if analysis else item_type,
        analysis.tags if analysis else (tags or []),
        source_type,
        capture_mode,
        capture_key=capture_key,
        source_url=source_url,
    )
    if analysis:
        await asyncio.to_thread(save_relation_candidates, saved.id, await relation_candidates(saved))
    return saved


async def analyze_and_save_capture(plan: CapturePlan) -> object:
    analyzed_items: list[CaptureItem] = []
    for item in plan.items:
        analysis = await analyze_brain_item(item.content, item.item_type)
        analyzed_items.append(
            replace(
                item,
                title=analysis.title if analysis else item.title,
                summary=analysis.summary if analysis else item.summary,
                item_type=analysis.item_type if analysis else item.item_type,
                tags=analysis.tags if analysis else item.tags,
            )
        )
    capture = await asyncio.to_thread(brain_store.save_capture, replace(plan, items=analyzed_items))
    for item in await asyncio.to_thread(brain_store.list_capture_items, capture.id):
        await asyncio.to_thread(save_relation_candidates, item.id, await relation_candidates(item))
    return capture
