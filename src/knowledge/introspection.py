import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.core.errors import try_async
from src.knowledge import brain_store
from src.knowledge.brain_analysis import (
    ask_brain_analysis,
    relation_candidates,
    save_relation_candidates,
)

logger = logging.getLogger(__name__)


INTROSPECTION_HOUR = 3
RECENT_ITEM_DAYS = 10
MAX_ELIGIBLE_ITEMS = 20


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def next_introspection_at(now: datetime, timezone_name: str) -> datetime:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    scheduled = local_now.replace(hour=INTROSPECTION_HOUR, minute=0, second=0, microsecond=0)
    if local_now >= scheduled:
        scheduled += timedelta(days=1)
    return scheduled.astimezone(timezone.utc)


def eligible_for_introspection(now: datetime) -> list[brain_store.BrainItem]:
    return brain_store.list_recent_items_for_organization(now, MAX_ELIGIBLE_ITEMS)


async def refresh_brain_connections(now: datetime) -> int | None:
    lock_token = brain_store.acquire_brain_organization_lock(now)
    if lock_token is None:
        return None

    async def refresh() -> int:
        refreshed = 0
        for item in eligible_for_introspection(now):
            if not brain_store.renew_brain_organization_lock(lock_token, utc_now()):
                logger.warning("Brain organization lock was lost before item %s", item.id)
                break
            if await _refresh_item(item, now):
                refreshed += 1
        return refreshed

    async def release_lock() -> None:
        brain_store.release_brain_organization_lock(lock_token)

    return await try_async(refresh, finally_handler=release_lock)


async def _refresh_item(item: brain_store.BrainItem, now: datetime) -> bool:
    async def refresh() -> bool:
        analysis = await ask_brain_analysis(item.content, item.item_type)
        if analysis is None:
            return False
        organized = brain_store.update_organized_metadata(item.id, analysis, now)
        if organized is None:
            return False
        candidates = await relation_candidates(organized)
        if candidates:
            save_relation_candidates(organized.id, candidates)
        return True

    async def log_failure(_: BaseException) -> bool:
        logger.exception("Unable to organize Brain item %s", item.id)
        return False

    return await try_async(refresh, handle_error=log_failure)
