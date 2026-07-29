import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Literal

from src.conversations.history import database_connection, format_timestamp

SourceStatus = Literal["pending", "ready", "unavailable"]


@dataclass(frozen=True)
class BrainItem:
    id: int
    content: str
    title: str
    summary: str
    item_type: str
    tags: list[str]
    source_type: str
    source_url: str | None
    source_status: SourceStatus | None
    source_published_at: datetime | None
    capture_mode: str
    status: str
    due_at: datetime | None
    timezone: str | None
    recurrence_rule: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class ScheduledDelivery:
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


def _create_brain_items_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS brain_items (
            id INTEGER PRIMARY KEY,
            content TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            item_type TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT,
            source_status TEXT,
            source_published_at TEXT,
            capture_mode TEXT NOT NULL,
            capture_key TEXT UNIQUE,
            status TEXT NOT NULL,
            due_at TEXT,
            timezone TEXT,
            recurrence_rule TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_recalled_at TEXT,
            completed_at TEXT
        )
        """
    )


def _create_scheduled_deliveries_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_deliveries (
            id INTEGER PRIMARY KEY,
            brain_item_id INTEGER NOT NULL REFERENCES brain_items(id),
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL,
            lease_expires_at TEXT,
            lease_token TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            delivered_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS scheduled_deliveries_due_index "
        "ON scheduled_deliveries(status, scheduled_at)"
    )


def _create_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX IF NOT EXISTS brain_items_status_created_index "
        "ON brain_items(status, created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS brain_items_type_status_index "
        "ON brain_items(item_type, status)"
    )


def _create_search_index(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS brain_items_fts "
        "USING fts5(title, summary, content, content='brain_items', content_rowid='id')"
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS brain_items_ai AFTER INSERT ON brain_items
        BEGIN
            INSERT INTO brain_items_fts(rowid, title, summary, content)
            VALUES (new.id, new.title, new.summary, new.content);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS brain_items_ad AFTER DELETE ON brain_items
        BEGIN
            INSERT INTO brain_items_fts(brain_items_fts, rowid, title, summary, content)
            VALUES ('delete', old.id, old.title, old.summary, old.content);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS brain_items_au AFTER UPDATE ON brain_items
        BEGIN
            INSERT INTO brain_items_fts(brain_items_fts, rowid, title, summary, content)
            VALUES ('delete', old.id, old.title, old.summary, old.content);
            INSERT INTO brain_items_fts(rowid, title, summary, content)
            VALUES (new.id, new.title, new.summary, new.content);
        END
        """
    )


def _initialize_schema(connection: sqlite3.Connection) -> None:
    _create_brain_items_table(connection)
    _create_scheduled_deliveries_table(connection)
    _create_indexes(connection)
    _create_search_index(connection)


@contextmanager
def _brain_database() -> Iterator[sqlite3.Connection]:
    with database_connection() as connection:
        _initialize_schema(connection)
        yield connection


def initialize_schema() -> None:
    with _brain_database():
        pass


def _optional_timestamp(value: str | None) -> datetime | None:
    return parse_timestamp(value) if value else None


def brain_item_from_row(row: sqlite3.Row) -> BrainItem:
    return BrainItem(
        id=row["id"],
        content=row["content"],
        title=row["title"],
        summary=row["summary"],
        item_type=row["item_type"],
        tags=json.loads(row["tags_json"]),
        source_type=row["source_type"],
        source_url=row["source_url"],
        source_status=row["source_status"],
        source_published_at=_optional_timestamp(row["source_published_at"]),
        capture_mode=row["capture_mode"],
        status=row["status"],
        due_at=_optional_timestamp(row["due_at"]),
        timezone=row["timezone"],
        recurrence_rule=row["recurrence_rule"],
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
        completed_at=_optional_timestamp(row["completed_at"]),
    )


def _source_status_for(
    source_url: str | None, source_status: SourceStatus | None
) -> SourceStatus | None:
    if source_status not in {None, "pending", "ready", "unavailable"}:
        raise ValueError("Unsupported source status.")
    if source_url is None:
        return None
    return source_status or "pending"


def save_item(
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
    source_status: SourceStatus | None = None,
    source_published_at: datetime | None = None,
    due_at: datetime | None = None,
    timezone_name: str | None = None,
    recurrence_rule: str | None = None,
) -> BrainItem:
    now = format_timestamp(utc_now())
    source_status = _source_status_for(source_url, source_status)
    with _brain_database() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO brain_items (
                content, title, summary, item_type, tags_json, source_type, source_url,
                source_status, source_published_at, capture_mode, capture_key, status,
                due_at, timezone, recurrence_rule, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                content.strip(),
                title.strip(),
                summary.strip(),
                item_type,
                json.dumps(tags),
                source_type,
                source_url,
                source_status,
                format_timestamp(source_published_at) if source_published_at else None,
                capture_mode,
                capture_key,
                format_timestamp(due_at) if due_at else None,
                timezone_name,
                recurrence_rule,
                now,
                now,
            ),
        )
        row_id = cursor.lastrowid
        if not cursor.rowcount:
            row_id = connection.execute(
                "SELECT id FROM brain_items WHERE capture_key = ?", (capture_key,)
            ).fetchone()["id"]
        row = connection.execute("SELECT * FROM brain_items WHERE id = ?", (row_id,)).fetchone()
    return brain_item_from_row(row)


def get_item(item_id: int) -> BrainItem | None:
    with _brain_database() as connection:
        row = connection.execute("SELECT * FROM brain_items WHERE id = ?", (item_id,)).fetchone()
    return brain_item_from_row(row) if row else None


def list_recent_items(limit: int = 20) -> list[BrainItem]:
    with _brain_database() as connection:
        rows = connection.execute("SELECT * FROM brain_items WHERE status = 'active' ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [brain_item_from_row(row) for row in rows]


def list_active_items(limit: int = 100) -> list[BrainItem]:
    return list_recent_items(limit)


def search_items(query: str, limit: int = 20, item_type: str | None = None) -> list[BrainItem]:
    terms = re.findall(r"[\w]+", query)
    if not terms:
        return []
    search_query = " OR ".join(terms)
    with _brain_database() as connection:
        rows = connection.execute(
            "SELECT brain_items.* FROM brain_items_fts JOIN brain_items ON brain_items.id = brain_items_fts.rowid "
            "WHERE brain_items_fts MATCH ? AND brain_items.status = 'active' "
            "AND (? IS NULL OR brain_items.item_type = ?) ORDER BY rank LIMIT ?",
            (search_query, item_type, item_type, limit),
        ).fetchall()
    return [brain_item_from_row(row) for row in rows]


def archive_item(item_id: int) -> bool:
    with _brain_database() as connection:
        cursor = connection.execute(
            "UPDATE brain_items SET status = 'archived', updated_at = ? WHERE id = ? AND status = 'active'",
            (format_timestamp(utc_now()), item_id),
        )
    return cursor.rowcount == 1


def delete_item(item_id: int) -> bool:
    with _brain_database() as connection:
        connection.execute("DELETE FROM scheduled_deliveries WHERE brain_item_id = ?", (item_id,))
        cursor = connection.execute("DELETE FROM brain_items WHERE id = ?", (item_id,))
    return cursor.rowcount == 1


def delete_active_item_with_title(item_id: int, title: str) -> str:
    with _brain_database() as connection:
        row = connection.execute("SELECT title FROM brain_items WHERE id = ? AND status = 'active'", (item_id,)).fetchone()
        if row is None:
            return "not_found"
        if row["title"] != title:
            return "mismatch"
        connection.execute("DELETE FROM scheduled_deliveries WHERE brain_item_id = ?", (item_id,))
        connection.execute("DELETE FROM brain_items WHERE id = ?", (item_id,))
    return "deleted"


def list_item_deliveries(item_id: int) -> list[dict[str, object]]:
    with _brain_database() as connection:
        rows = connection.execute(
            "SELECT scheduled_deliveries.*, brain_items.title FROM scheduled_deliveries "
            "JOIN brain_items ON brain_items.id = scheduled_deliveries.brain_item_id "
            "WHERE brain_item_id = ? ORDER BY scheduled_deliveries.id", (item_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def export_brain_data() -> dict[str, object]:
    with _brain_database() as connection:
        return {
            "brain_items": [dict(row) for row in connection.execute("SELECT * FROM brain_items ORDER BY id")],
            "scheduled_deliveries": [dict(row) for row in connection.execute("SELECT * FROM scheduled_deliveries ORDER BY id")],
        }


def delete_all_brain_data() -> None:
    with _brain_database() as connection:
        connection.execute("DELETE FROM scheduled_deliveries")
        connection.execute("DELETE FROM brain_items")
