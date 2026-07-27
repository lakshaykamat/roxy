import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.conversations.history import database_connection, format_timestamp


BRAIN_ITEM_TYPES = frozenset(
    {
        "idea", "fact", "preference", "person", "project", "goal", "decision",
        "task", "reference", "reflection",
    }
)


@dataclass(frozen=True)
class BrainItem:
    id: int
    content: str
    title: str
    summary: str
    item_type: str
    tags: list[str]
    source_type: str
    capture_mode: str
    status: str
    due_at: datetime | None
    timezone: str | None
    recurrence_rule: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

@dataclass(frozen=True)
class ReminderDelivery:
    id: int
    brain_item_id: int
    title: str
    scheduled_at: datetime
    attempt_count: int
    lease_token: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS brain_settings ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), auto_capture_enabled INTEGER NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS brain_items ("
        "id INTEGER PRIMARY KEY, content TEXT NOT NULL, title TEXT NOT NULL, "
        "summary TEXT NOT NULL, item_type TEXT NOT NULL, tags_json TEXT NOT NULL, "
        "source_type TEXT NOT NULL, source_url TEXT, capture_mode TEXT NOT NULL, "
        "capture_key TEXT UNIQUE, status TEXT NOT NULL, due_at TEXT, timezone TEXT, "
        "recurrence_rule TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "last_recalled_at TEXT, completed_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS reminder_deliveries ("
        "id INTEGER PRIMARY KEY, brain_item_id INTEGER NOT NULL REFERENCES brain_items(id), "
        "scheduled_at TEXT NOT NULL, status TEXT NOT NULL, lease_expires_at TEXT, "
        "lease_token TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT, "
        "delivered_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS brain_items_status_created_index "
        "ON brain_items(status, created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS brain_items_type_status_index "
        "ON brain_items(item_type, status)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS reminder_deliveries_due_index "
        "ON reminder_deliveries(status, scheduled_at)"
    )
    connection.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS brain_items_fts "
        "USING fts5(title, summary, content, content='brain_items', content_rowid='id')"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS brain_items_ai AFTER INSERT ON brain_items "
        "BEGIN INSERT INTO brain_items_fts(rowid, title, summary, content) "
        "VALUES (new.id, new.title, new.summary, new.content); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS brain_items_ad AFTER DELETE ON brain_items "
        "BEGIN INSERT INTO brain_items_fts(brain_items_fts, rowid, title, summary, content) "
        "VALUES ('delete', old.id, old.title, old.summary, old.content); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS brain_items_au AFTER UPDATE ON brain_items "
        "BEGIN INSERT INTO brain_items_fts(brain_items_fts, rowid, title, summary, content) "
        "VALUES ('delete', old.id, old.title, old.summary, old.content); "
        "INSERT INTO brain_items_fts(rowid, title, summary, content) "
        "VALUES (new.id, new.title, new.summary, new.content); END"
    )
    connection.execute(
        "INSERT OR IGNORE INTO brain_settings (id, auto_capture_enabled, updated_at) "
        "VALUES (1, 1, ?)",
        (format_timestamp(utc_now()),),
    )


def _migrate_legacy_data(connection: sqlite3.Connection) -> None:
    tables = _tables(connection)
    if not {"memories", "tasks", "reminders", "service_heartbeats"} & tables:
        return
    task_ids: dict[int, int] = {}
    if "memories" in tables:
        for row in connection.execute("SELECT * FROM memories ORDER BY id"):
            connection.execute(
                "INSERT INTO brain_items "
                "(content, title, summary, item_type, tags_json, source_type, "
                "capture_mode, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '[]', 'command', 'explicit', 'active', ?, ?)",
                (
                    row["content"], row["content"], row["content"], row["kind"],
                    row["created_at"], row["created_at"],
                ),
            )
    if "tasks" in tables:
        for row in connection.execute("SELECT * FROM tasks ORDER BY id"):
            cursor = connection.execute(
                "INSERT INTO brain_items "
                "(content, title, summary, item_type, tags_json, source_type, "
                "capture_mode, status, due_at, timezone, recurrence_rule, created_at, "
                "updated_at, completed_at) "
                "VALUES (?, ?, ?, 'task', '[]', 'command', 'explicit', ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["title"], row["title"], row["title"], row["status"],
                    row["next_due_at"], row["timezone"], row["recurrence_rule"],
                    row["created_at"], row["completed_at"] or row["created_at"],
                    row["completed_at"],
                ),
            )
            task_ids[row["id"]] = cursor.lastrowid
    if "reminders" in tables:
        for row in connection.execute("SELECT * FROM reminders ORDER BY id"):
            item_id = task_ids.get(row["task_id"])
            if item_id is not None:
                connection.execute(
                    "INSERT INTO reminder_deliveries "
                    "(id, brain_item_id, scheduled_at, status, lease_expires_at, "
                    "lease_token, attempt_count, last_error, delivered_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["id"], item_id, row["scheduled_at"], row["status"],
                        row["lease_expires_at"],
                        row["lease_token"] if "lease_token" in row.keys() else None,
                        row["attempt_count"], row["last_error"], row["delivered_at"],
                        row["created_at"], row["updated_at"],
                    ),
                )
    for name in ("reminders", "tasks", "memories", "service_heartbeats"):
        if name in tables:
            connection.execute(f"DROP TABLE {name}")


def initialize_schema() -> None:
    with database_connection() as connection:
        _initialize_schema(connection)
        _migrate_legacy_data(connection)


def item_from_row(row: sqlite3.Row) -> BrainItem:
    return BrainItem(
        id=row["id"],
        content=row["content"],
        title=row["title"],
        summary=row["summary"],
        item_type=row["item_type"],
        tags=json.loads(row["tags_json"]),
        source_type=row["source_type"],
        capture_mode=row["capture_mode"],
        status=row["status"],
        due_at=parse_timestamp(row["due_at"]) if row["due_at"] else None,
        timezone=row["timezone"],
        recurrence_rule=row["recurrence_rule"],
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
        completed_at=parse_timestamp(row["completed_at"]) if row["completed_at"] else None,
    )


def create_item(
    content: str,
    title: str,
    summary: str,
    item_type: str,
    tags: list[str],
    source_type: str,
    capture_mode: str,
    *,
    capture_key: str | None = None,
    source_url: str | None = None,
    due_at: datetime | None = None,
    timezone_name: str | None = None,
    recurrence_rule: str | None = None,
) -> BrainItem:
    now = format_timestamp(utc_now())
    with database_connection() as connection:
        _initialize_schema(connection)
        _migrate_legacy_data(connection)
        cursor = connection.execute(
            "INSERT OR IGNORE INTO brain_items "
            "(content, title, summary, item_type, tags_json, source_type, source_url, capture_mode, "
            "capture_key, status, due_at, timezone, recurrence_rule, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
            (
                content.strip(), title.strip(), summary.strip(), item_type, json.dumps(tags),
                source_type, source_url, capture_mode, capture_key,
                format_timestamp(due_at) if due_at else None,
                timezone_name, recurrence_rule, now, now,
            ),
        )
        if cursor.rowcount:
            row_id = cursor.lastrowid
        else:
            row_id = connection.execute(
                "SELECT id FROM brain_items WHERE capture_key = ?", (capture_key,)
            ).fetchone()["id"]
        row = connection.execute("SELECT * FROM brain_items WHERE id = ?", (row_id,)).fetchone()
    return item_from_row(row)


def get_item(item_id: int) -> BrainItem | None:
    initialize_schema()
    with database_connection() as connection:
        row = connection.execute("SELECT * FROM brain_items WHERE id = ?", (item_id,)).fetchone()
    return item_from_row(row) if row else None


def list_recent_items(limit: int = 20) -> list[BrainItem]:
    initialize_schema()
    with database_connection() as connection:
        rows = connection.execute("SELECT * FROM brain_items WHERE status = 'active' ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [item_from_row(row) for row in rows]


def brain_graph_data() -> dict[str, list[dict[str, object]]]:
    initialize_schema()
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT id, title, summary, item_type, tags_json, source_url "
            "FROM brain_items WHERE status = 'active' ORDER BY created_at, id"
        ).fetchall()

    nodes = [
        {
            "id": row["id"],
            "title": row["title"],
            "summary": row["summary"],
            "item_type": row["item_type"],
            "tags": json.loads(row["tags_json"]),
            "source_url": row["source_url"],
        }
        for row in rows
    ]
    edges: list[dict[str, object]] = []
    for index, source in enumerate(nodes):
        source_tags = set(source["tags"])
        for target in nodes[index + 1:]:
            shared_tags = sorted(source_tags & set(target["tags"]))
            if shared_tags:
                edges.append(
                    {"source": source["id"], "target": target["id"], "tags": shared_tags}
                )
    return {"nodes": nodes, "edges": edges}


def search_items(
    query: str, limit: int = 20, item_type: str | None = None
) -> list[BrainItem]:
    initialize_schema()
    with database_connection() as connection:
        terms = " OR ".join(f'"{term}"' for term in query.split() if term)
        rows = connection.execute(
            "SELECT brain_items.* FROM brain_items_fts "
            "JOIN brain_items ON brain_items.id = brain_items_fts.rowid "
            "WHERE brain_items_fts MATCH ? AND brain_items.status = 'active' "
            "AND (? IS NULL OR brain_items.item_type = ?) ORDER BY rank LIMIT ?",
            (terms, item_type, item_type, limit),
        ).fetchall() if terms else []
    return [item_from_row(row) for row in rows]


def auto_capture_enabled() -> bool:
    initialize_schema()
    with database_connection() as connection:
        row = connection.execute(
            "SELECT auto_capture_enabled FROM brain_settings WHERE id = 1"
        ).fetchone()
    return bool(row["auto_capture_enabled"])


def set_auto_capture_enabled(enabled: bool) -> None:
    initialize_schema()
    with database_connection() as connection:
        connection.execute(
            "UPDATE brain_settings SET auto_capture_enabled = ?, updated_at = ? WHERE id = 1",
            (int(enabled), format_timestamp(utc_now())),
        )


def archive_item(item_id: int) -> bool:
    initialize_schema()
    with database_connection() as connection:
        cursor = connection.execute(
            "UPDATE brain_items SET status = 'archived', updated_at = ? "
            "WHERE id = ? AND status = 'active'",
            (format_timestamp(utc_now()), item_id),
        )
    return cursor.rowcount == 1


def list_deliveries_for_item(item_id: int) -> list[ReminderDelivery]:
    initialize_schema()
    with database_connection() as connection:
        rows = connection.execute("SELECT reminder_deliveries.*, brain_items.title FROM reminder_deliveries JOIN brain_items ON brain_items.id = reminder_deliveries.brain_item_id WHERE brain_item_id = ? ORDER BY reminder_deliveries.id", (item_id,)).fetchall()
    return [ReminderDelivery(row["id"], row["brain_item_id"], row["title"], parse_timestamp(row["scheduled_at"]), row["attempt_count"], row["lease_token"] or "") for row in rows]


def export_brain_data() -> dict[str, object]:
    initialize_schema()
    with database_connection() as connection:
        return {
            "brain_settings": dict(
                connection.execute("SELECT * FROM brain_settings WHERE id = 1").fetchone()
            ),
            "brain_items": [dict(row) for row in connection.execute("SELECT * FROM brain_items ORDER BY id")],
            "reminder_deliveries": [
                dict(row)
                for row in connection.execute("SELECT * FROM reminder_deliveries ORDER BY id")
            ],
        }


def delete_all_brain_data() -> None:
    initialize_schema()
    with database_connection() as connection:
        connection.execute("DELETE FROM reminder_deliveries")
        connection.execute("DELETE FROM brain_items")
        connection.execute("DELETE FROM brain_settings")


def delete_item(item_id: int) -> bool:
    initialize_schema()
    with database_connection() as connection:
        connection.execute("DELETE FROM reminder_deliveries WHERE brain_item_id = ?", (item_id,))
        cursor = connection.execute("DELETE FROM brain_items WHERE id = ?", (item_id,))
    return cursor.rowcount == 1
