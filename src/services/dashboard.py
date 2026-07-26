import sqlite3
from datetime import datetime, timedelta, timezone

from src import config
from src.utils import heartbeats, memory
from src.utils.database import read_only_database_connection
from src.utils.history import format_timestamp


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_error(value: str | None, limit: int = 160) -> str:
    return "Delivery failed. Check service logs for details."[:limit]


def _count_by_value(
    connection: sqlite3.Connection, query: str, defaults: dict[str, int]
) -> dict[str, int]:
    counts = defaults.copy()
    for row in connection.execute(query):
        counts[row["value"]] = row["count"]
    return counts


def _service_snapshot(current_time: datetime) -> dict[str, dict[str, str | None]]:
    recorded = heartbeats.get_heartbeats(current_time)
    services: dict[str, dict[str, str | None]] = {}
    for service_name in ("bot", "worker"):
        heartbeat = recorded.get(service_name)
        services[service_name] = {
            "status": heartbeat.status if heartbeat else "unhealthy",
            "updated_at": format_timestamp(heartbeat.updated_at) if heartbeat else None,
        }
    return services


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def get_dashboard_snapshot(now: datetime | None = None) -> dict[str, object]:
    current_time = (now or utc_now()).astimezone(timezone.utc)
    cutoff = format_timestamp(current_time - timedelta(days=1))
    seven_days_from_now = format_timestamp(current_time + timedelta(days=7))
    current_timestamp = format_timestamp(current_time)

    with read_only_database_connection() as connection:
        message_exists = _table_exists(connection, "messages")
        memory_exists = _table_exists(connection, "memories")
        task_exists = _table_exists(connection, "tasks")
        reminder_exists = _table_exists(connection, "reminders")
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
            connection, "SELECT role AS value, COUNT(*) AS count FROM messages GROUP BY role",
            {"assistant": 0, "user": 0},
        ) if message_exists else {"assistant": 0, "user": 0}
        empty_memory_kinds = {kind: 0 for kind in sorted(memory.MEMORY_KINDS)}
        memory_kinds = _count_by_value(
            connection, "SELECT kind AS value, COUNT(*) AS count FROM memories GROUP BY kind",
            empty_memory_kinds,
        ) if memory_exists else empty_memory_kinds
        memory_total = sum(memory_kinds.values())
        expiring_memories = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE expires_at IS NOT NULL "
            "AND expires_at > ? AND expires_at <= ?", (current_timestamp, seven_days_from_now)
        ).fetchone()[0] if memory_exists else 0
        task_statuses = _count_by_value(
            connection, "SELECT status AS value, COUNT(*) AS count FROM tasks GROUP BY status",
            {"active": 0, "completed": 0, "cancelled": 0},
        ) if task_exists else {"active": 0, "completed": 0, "cancelled": 0}
        reminder_statuses = _count_by_value(
            connection, "SELECT status AS value, COUNT(*) AS count FROM reminders GROUP BY status",
            {"pending": 0, "leased": 0, "delivered": 0, "failed": 0},
        ) if reminder_exists else {"pending": 0, "leased": 0, "delivered": 0, "failed": 0}
        overdue_pending = connection.execute(
            "SELECT COUNT(*) FROM reminders WHERE status = 'pending' AND scheduled_at < ?",
            (current_timestamp,),
        ).fetchone()[0] if reminder_exists else 0
        upcoming_rows = connection.execute(
            "SELECT tasks.title, reminders.scheduled_at, tasks.recurrence_rule "
            "FROM reminders JOIN tasks ON tasks.id = reminders.task_id "
            "WHERE reminders.status = 'pending' AND reminders.scheduled_at >= ? "
            "ORDER BY reminders.scheduled_at, reminders.id LIMIT 5",
            (current_timestamp,),
        ).fetchall() if reminder_exists and task_exists else []
        failure_rows = connection.execute(
            "SELECT tasks.title, reminders.updated_at, reminders.attempt_count, reminders.last_error "
            "FROM reminders JOIN tasks ON tasks.id = reminders.task_id "
            "WHERE reminders.status = 'failed' ORDER BY reminders.updated_at DESC, reminders.id DESC LIMIT 5"
        ).fetchall() if reminder_exists and task_exists else []

    services = _service_snapshot(current_time)
    statuses = {service["status"] for service in services.values()}
    overall_status = (
        "healthy"
        if statuses == {"healthy"}
        else "degraded"
        if "healthy" in statuses
        else "unhealthy"
    )
    return {
        "generated_at": current_timestamp,
        "status": overall_status,
        "services": services,
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
                    "error": sanitize_error(row["last_error"]),
                }
                for row in failure_rows
            ],
        },
        "notices": [],
    }
