import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal

from src import config
from src.knowledge import brain_store
from src.knowledge.constants import BRAIN_ITEM_TYPES
from src.core.database import read_only_database_connection
from src.conversations.history import format_timestamp

DELIVERY_FAILURE_MESSAGE = "Delivery failed. Check service logs for details."
MESSAGE_ROLE_COUNTS = {"assistant": 0, "user": 0}
TASK_STATUS_COUNTS = {"active": 0, "completed": 0, "cancelled": 0}
DELIVERY_STATUS_COUNTS = {"pending": 0, "leased": 0, "delivered": 0, "failed": 0}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _count_by_value(
    connection: sqlite3.Connection, query: str, defaults: dict[str, int]
) -> dict[str, int]:
    counts = defaults.copy()
    for row in connection.execute(query):
        counts[row["value"]] = row["count"]
    return counts


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def _source_state(item: brain_store.BrainItem) -> str:
    if item.source_url and item.item_type == "reference":
        return "bookmark" if item.summary == "Saved link" else "analyzed"
    return "saved"


def get_brain_snapshot() -> dict[str, object]:
    return {
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "summary": item.summary,
                "item_type": item.item_type,
                "tags": item.tags,
                "source_url": item.source_url,
                "source_status": item.source_status,
                "source_state": _source_state(item),
                "captured_at": item.created_at.isoformat(),
                "source_published_at": (
                    item.source_published_at.isoformat()
                    if item.source_published_at
                    else None
                ),
            }
            for item in brain_store.list_recent_items(limit=100)
        ]
    }


def archive_brain_item(item_id: int) -> bool:
    return brain_store.archive_item(item_id)


def delete_brain_item(item_id: int, title: str) -> Literal["deleted", "mismatch", "not_found"]:
    return brain_store.delete_active_item_with_title(item_id, title)


def get_dashboard_snapshot(now: datetime | None = None) -> dict[str, object]:
    current_time = (now or utc_now()).astimezone(timezone.utc)
    cutoff = format_timestamp(current_time - timedelta(days=1))
    current_timestamp = format_timestamp(current_time)

    with read_only_database_connection() as connection:
        message_exists = _table_exists(connection, "messages")
        brain_exists = _table_exists(connection, "brain_items")
        delivery_exists = _table_exists(connection, "scheduled_deliveries")
        message_total = (
            connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if message_exists else 0
        )
        message_last_day = (
            connection.execute("SELECT COUNT(*) FROM messages WHERE created_at >= ?", (cutoff,)).fetchone()[0]
            if message_exists else 0
        )
        latest_message = (
            connection.execute("SELECT MAX(created_at) FROM messages").fetchone()[0]
            if message_exists else None
        )
        message_roles = _count_by_value(
            connection,
            "SELECT role AS value, COUNT(*) AS count FROM messages GROUP BY role",
            MESSAGE_ROLE_COUNTS,
        ) if message_exists else MESSAGE_ROLE_COUNTS.copy()
        empty_memory_kinds = {kind: 0 for kind in sorted(BRAIN_ITEM_TYPES - {"task"})}
        memory_kinds = _count_by_value(
            connection,
            "SELECT item_type AS value, COUNT(*) AS count FROM brain_items "
            "WHERE item_type != 'task' GROUP BY item_type",
            empty_memory_kinds,
        ) if brain_exists else empty_memory_kinds
        memory_total = sum(memory_kinds.values())
        expiring_memories = 0
        task_statuses = _count_by_value(
            connection,
            "SELECT status AS value, COUNT(*) AS count FROM brain_items "
            "WHERE item_type = 'task' GROUP BY status",
            TASK_STATUS_COUNTS,
        ) if brain_exists else TASK_STATUS_COUNTS.copy()
        reminder_statuses = _count_by_value(
            connection,
            "SELECT status AS value, COUNT(*) AS count FROM scheduled_deliveries GROUP BY status",
            DELIVERY_STATUS_COUNTS,
        ) if delivery_exists else DELIVERY_STATUS_COUNTS.copy()
        overdue_pending = connection.execute(
            "SELECT COUNT(*) FROM scheduled_deliveries "
            "WHERE status = 'pending' AND scheduled_at < ?",
            (current_timestamp,),
        ).fetchone()[0] if delivery_exists else 0
        upcoming_rows = connection.execute(
            "SELECT brain_items.title, scheduled_deliveries.scheduled_at, "
            "brain_items.recurrence_rule FROM scheduled_deliveries "
            "JOIN brain_items ON brain_items.id = scheduled_deliveries.brain_item_id "
            "WHERE scheduled_deliveries.status = 'pending' "
            "AND scheduled_deliveries.scheduled_at >= ? "
            "ORDER BY scheduled_deliveries.scheduled_at, scheduled_deliveries.id LIMIT 5",
            (current_timestamp,),
        ).fetchall() if delivery_exists and brain_exists else []
        failure_rows = connection.execute(
            "SELECT brain_items.title, scheduled_deliveries.updated_at, "
            "scheduled_deliveries.attempt_count, scheduled_deliveries.last_error "
            "FROM scheduled_deliveries "
            "JOIN brain_items ON brain_items.id = scheduled_deliveries.brain_item_id "
            "WHERE scheduled_deliveries.status = 'failed' "
            "ORDER BY scheduled_deliveries.updated_at DESC, scheduled_deliveries.id DESC LIMIT 5"
        ).fetchall() if delivery_exists and brain_exists else []

    return {
        "generated_at": current_timestamp,
        "status": "healthy",
        "services": {},
        "configuration": {
            "openai_model": config.OPENAI_MODEL,
            "transcription_model": config.OPENAI_TRANSCRIPTION_MODEL,
            "task_timezone": config.TASK_TIMEZONE,
            "history_retention_days": config.HISTORY_RETENTION_DAYS,
            "memory_retention_days": config.MEMORY_RETENTION_DAYS,
            "expense_tracker_enabled": config.EXPENSE_TRACKER_ENABLED,
        },
        "messages": {
            "total": message_total,
            "last_24_hours": message_last_day,
            "by_role": message_roles,
            "latest_at": latest_message,
        },
        "memories": {
            "total": memory_total,
            "by_kind": memory_kinds,
            "expiring_within_7_days": expiring_memories,
        },
        "tasks": {"by_status": task_statuses},
        "reminders": {
            "by_status": reminder_statuses,
            "overdue_pending": overdue_pending,
            "upcoming": [
                {
                    "title": row["title"],
                    "scheduled_at": row["scheduled_at"],
                    "recurrence": row["recurrence_rule"],
                }
                for row in upcoming_rows
            ],
            "recent_failures": [
                {
                    "title": row["title"],
                    "updated_at": row["updated_at"],
                    "attempt_count": row["attempt_count"],
                    "error": DELIVERY_FAILURE_MESSAGE,
                }
                for row in failure_rows
            ],
        },
        "notices": [],
    }
