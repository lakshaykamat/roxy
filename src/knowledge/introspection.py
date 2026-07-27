from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.knowledge import brain_store
from src.knowledge.brain_analysis import relation_candidates, save_relation_candidates


INTROSPECTION_HOUR = 3
RECENT_ITEM_DAYS = 30
MAX_ELIGIBLE_ITEMS = 100
BATCH_SIZE = 20


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def next_introspection_at(now: datetime, timezone_name: str) -> datetime:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    scheduled = local_now.replace(hour=INTROSPECTION_HOUR, minute=0, second=0, microsecond=0)
    if local_now >= scheduled:
        scheduled += timedelta(days=1)
    return scheduled.astimezone(timezone.utc)


def eligible_for_introspection(now: datetime) -> list[brain_store.BrainItem]:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=RECENT_ITEM_DAYS)
    recent = [item for item in brain_store.list_active_items(MAX_ELIGIBLE_ITEMS) if item.created_at >= cutoff]
    unconnected = brain_store.list_unconnected_active_items()
    return list({item.id: item for item in [*recent, *unconnected]}.values())[:MAX_ELIGIBLE_ITEMS]


async def refresh_brain_connections(now: datetime) -> int:
    refreshed = 0
    items = eligible_for_introspection(now)
    for start in range(0, len(items), BATCH_SIZE):
        for item in items[start:start + BATCH_SIZE]:
            candidates = await relation_candidates(item)
            if candidates:
                save_relation_candidates(item.id, candidates)
                refreshed += len(candidates)
    return refreshed
