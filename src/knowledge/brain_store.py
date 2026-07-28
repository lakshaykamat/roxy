import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterator, Literal

from src.conversations.history import database_connection, format_timestamp
from src.knowledge.capture_planner import Capture, CapturePlan
from src.knowledge.constants import BRAIN_ITEM_TYPES, RELATION_TYPES

if TYPE_CHECKING:
    from src.knowledge.brain_analysis import BrainAnalysis


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
    source_published_at: datetime | None
    capture_mode: str
    status: str
    due_at: datetime | None
    timezone: str | None
    recurrence_rule: str | None
    created_at: datetime
    updated_at: datetime
    last_organized_at: datetime | None
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


def _create_brain_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS brain_settings ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), auto_capture_enabled INTEGER NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS brain_items ("
        "id INTEGER PRIMARY KEY, content TEXT NOT NULL, title TEXT NOT NULL, "
        "summary TEXT NOT NULL, item_type TEXT NOT NULL, tags_json TEXT NOT NULL, "
        "source_type TEXT NOT NULL, source_url TEXT, source_published_at TEXT, capture_mode TEXT NOT NULL, "
        "capture_key TEXT UNIQUE, status TEXT NOT NULL, due_at TEXT, timezone TEXT, "
        "recurrence_rule TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "last_recalled_at TEXT, last_organized_at TEXT, completed_at TEXT)"
    )


def _add_missing_brain_item_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(brain_items)")
    }
    if "last_organized_at" not in columns:
        connection.execute("ALTER TABLE brain_items ADD COLUMN last_organized_at TEXT")


def _create_capture_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS brain_captures ("
        "id INTEGER PRIMARY KEY, request TEXT NOT NULL, analysis TEXT NOT NULL, "
        "rationale TEXT NOT NULL, captured_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS brain_capture_items ("
        "capture_id INTEGER NOT NULL REFERENCES brain_captures(id), "
        "brain_item_id INTEGER NOT NULL REFERENCES brain_items(id), "
        "PRIMARY KEY (capture_id, brain_item_id))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS brain_item_relations ("
        "source_item_id INTEGER NOT NULL REFERENCES brain_items(id), "
        "target_item_id INTEGER NOT NULL REFERENCES brain_items(id), "
        "relation_type TEXT NOT NULL, explanation TEXT NOT NULL, confidence REAL NOT NULL, "
        "origin TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "PRIMARY KEY (source_item_id, target_item_id, relation_type))"
    )


def _create_reminder_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS reminder_deliveries ("
        "id INTEGER PRIMARY KEY, brain_item_id INTEGER NOT NULL REFERENCES brain_items(id), "
        "scheduled_at TEXT NOT NULL, status TEXT NOT NULL, lease_expires_at TEXT, "
        "lease_token TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT, "
        "delivered_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )


def _create_organization_lock_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS brain_organization_lock ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), lock_token TEXT NOT NULL, "
        "lease_expires_at TEXT NOT NULL)"
    )


def _create_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX IF NOT EXISTS brain_captures_captured_at_index "
        "ON brain_captures(captured_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS brain_relations_endpoints_index "
        "ON brain_item_relations(source_item_id, target_item_id)"
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


def _create_search_index(connection: sqlite3.Connection) -> None:
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


def _initialize_settings(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO brain_settings (id, auto_capture_enabled, updated_at) "
        "VALUES (1, 1, ?)",
        (format_timestamp(utc_now()),),
    )


def _initialize_schema(connection: sqlite3.Connection) -> None:
    _create_brain_tables(connection)
    _add_missing_brain_item_columns(connection)
    _create_capture_tables(connection)
    _create_reminder_table(connection)
    _create_organization_lock_table(connection)
    _create_indexes(connection)
    _create_search_index(connection)
    _initialize_settings(connection)


@contextmanager
def _brain_database() -> Iterator[sqlite3.Connection]:
    with database_connection() as connection:
        _initialize_schema(connection)
        yield connection


def initialize_schema() -> None:
    with _brain_database():
        pass


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
        source_published_at=(
            parse_timestamp(row["source_published_at"])
            if row["source_published_at"] else None
        ),
        capture_mode=row["capture_mode"],
        status=row["status"],
        due_at=parse_timestamp(row["due_at"]) if row["due_at"] else None,
        timezone=row["timezone"],
        recurrence_rule=row["recurrence_rule"],
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
        last_organized_at=(
            parse_timestamp(row["last_organized_at"])
            if row["last_organized_at"] else None
        ),
        completed_at=parse_timestamp(row["completed_at"]) if row["completed_at"] else None,
    )


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
    source_published_at: datetime | None = None,
    due_at: datetime | None = None,
    timezone_name: str | None = None,
    recurrence_rule: str | None = None,
) -> BrainItem:
    now = format_timestamp(utc_now())
    with _brain_database() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO brain_items "
            "(content, title, summary, item_type, tags_json, source_type, source_url, source_published_at, capture_mode, "
            "capture_key, status, due_at, timezone, recurrence_rule, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
            (
                content.strip(), title.strip(), summary.strip(), item_type, json.dumps(tags),
                source_type, source_url,
                format_timestamp(source_published_at) if source_published_at else None,
                capture_mode, capture_key,
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
    return brain_item_from_row(row)


def save_capture(plan: CapturePlan) -> Capture:
    captured_at = format_timestamp(utc_now())
    with _brain_database() as connection:
        cursor = connection.execute(
            "INSERT INTO brain_captures (request, analysis, rationale, captured_at) VALUES (?, ?, ?, ?)",
            (plan.request, plan.analysis, plan.rationale, captured_at),
        )
        capture_id = cursor.lastrowid
        item_ids: list[int] = []
        for planned_item in plan.items:
            item_cursor = connection.execute(
                "INSERT INTO brain_items "
                "(content, title, summary, item_type, tags_json, source_type, source_url, "
                "source_published_at, capture_mode, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'capture', ?, ?, 'explicit', 'active', ?, ?)",
                (
                    planned_item.content.strip(), planned_item.title.strip(),
                    planned_item.summary.strip(), planned_item.item_type,
                    json.dumps(planned_item.tags), planned_item.source_url,
                    planned_item.source_published_at, captured_at, captured_at,
                ),
            )
            connection.execute(
                "INSERT INTO brain_capture_items (capture_id, brain_item_id) VALUES (?, ?)",
                (capture_id, item_cursor.lastrowid),
            )
            item_ids.append(item_cursor.lastrowid)
        for relation in plan.relations:
            if not isinstance(relation.source_item_index, int) or relation.source_item_index < 0:
                raise ValueError("Relation source item index must identify a captured item.")
            if relation.source_item_index >= len(item_ids):
                raise ValueError("Relation source item index must identify a captured item.")
            _create_relation(
                connection, item_ids[relation.source_item_index], relation.target_item_id,
                relation.relation_type, relation.explanation, relation.confidence, "planner",
            )
    return Capture(capture_id, plan.request, plan.analysis, plan.rationale, captured_at)


def list_capture_items(capture_id: int) -> list[BrainItem]:
    with _brain_database() as connection:
        rows = connection.execute(
            "SELECT brain_items.* FROM brain_items JOIN brain_capture_items "
            "ON brain_capture_items.brain_item_id = brain_items.id "
            "WHERE brain_capture_items.capture_id = ? ORDER BY brain_items.id",
            (capture_id,),
        ).fetchall()
    return [brain_item_from_row(row) for row in rows]


def create_relation(
    source_id: int, target_id: int, relation_type: str, explanation: str,
    confidence: float, origin: str,
) -> None:
    with _brain_database() as connection:
        _create_relation(connection, source_id, target_id, relation_type, explanation, confidence, origin)


def _create_relation(
    connection: sqlite3.Connection, source_id: int, target_id: int, relation_type: str,
    explanation: str, confidence: float, origin: str,
) -> None:
    if relation_type not in RELATION_TYPES:
        raise ValueError("Unsupported brain relation type.")
    if not explanation.strip():
        raise ValueError("Relation explanation is required.")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("Relation confidence must be between 0 and 1.")
    now = format_timestamp(utc_now())
    connection.execute(
        "INSERT INTO brain_item_relations "
        "(source_item_id, target_item_id, relation_type, explanation, confidence, origin, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source_item_id, target_item_id, relation_type) DO UPDATE SET "
        "explanation = excluded.explanation, confidence = excluded.confidence, "
        "origin = excluded.origin, updated_at = excluded.updated_at",
        (source_id, target_id, relation_type, explanation.strip(), confidence, origin, now, now),
    )


def list_item_relations(item_id: int) -> list[dict[str, object]]:
    with _brain_database() as connection:
        rows = connection.execute(
            "SELECT * FROM brain_item_relations WHERE source_item_id = ? OR target_item_id = ? "
            "ORDER BY updated_at DESC", (item_id, item_id)
        ).fetchall()
    return [dict(row) for row in rows]


def get_item_capture_context(item_id: int) -> dict[str, object]:
    with _brain_database() as connection:
        capture = connection.execute(
            "SELECT brain_captures.analysis, brain_captures.captured_at "
            "FROM brain_captures JOIN brain_capture_items "
            "ON brain_capture_items.capture_id = brain_captures.id "
            "WHERE brain_capture_items.brain_item_id = ? "
            "ORDER BY brain_captures.captured_at DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        relations = connection.execute(
            "SELECT brain_item_relations.*, "
            "CASE WHEN source_item_id = ? THEN target_item_id ELSE source_item_id END AS related_item_id, "
            "brain_items.title AS related_item_title "
            "FROM brain_item_relations JOIN brain_items "
            "ON brain_items.id = CASE WHEN source_item_id = ? THEN target_item_id ELSE source_item_id END "
            "AND brain_items.status = 'active' "
            "WHERE source_item_id = ? OR target_item_id = ? ORDER BY updated_at DESC",
            (item_id, item_id, item_id, item_id),
        ).fetchall()
    return {
        "summary": capture["analysis"] if capture else None,
        "captured_at": capture["captured_at"] if capture else None,
        "relations": [dict(relation) for relation in relations],
    }


def list_capture_timeline(limit: int = 20) -> list[dict[str, object]]:
    with _brain_database() as connection:
        captures = connection.execute(
            "SELECT * FROM brain_captures ORDER BY captured_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {
            **dict(capture),
            "items": [
                {"id": item.id, "title": item.title, "summary": item.summary}
                for item in list_capture_items(capture["id"])
            ],
        }
        for capture in captures
    ]


def get_item(item_id: int) -> BrainItem | None:
    with _brain_database() as connection:
        row = connection.execute("SELECT * FROM brain_items WHERE id = ?", (item_id,)).fetchone()
    return brain_item_from_row(row) if row else None


def list_recent_items(limit: int = 20) -> list[BrainItem]:
    with _brain_database() as connection:
        rows = connection.execute("SELECT * FROM brain_items WHERE status = 'active' ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [brain_item_from_row(row) for row in rows]


def get_brain_graph() -> dict[str, list[dict[str, object]]]:
    with _brain_database() as connection:
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
    with _brain_database() as connection:
        edges = [
            dict(row)
            for row in connection.execute(
                "SELECT source_item_id AS source, target_item_id AS target, relation_type, "
                "explanation, confidence, origin FROM brain_item_relations "
                "WHERE source_item_id IN (SELECT id FROM brain_items WHERE status = 'active') "
                "AND target_item_id IN (SELECT id FROM brain_items WHERE status = 'active') "
                "ORDER BY updated_at DESC"
            )
        ]
    return {"nodes": nodes, "edges": edges}


def list_active_items(limit: int = 100) -> list[BrainItem]:
    return list_recent_items(limit)


def list_recent_items_for_organization(
    now: datetime, limit: int = 20
) -> list[BrainItem]:
    cutoff = format_timestamp(now.astimezone(timezone.utc) - timedelta(days=10))
    with _brain_database() as connection:
        rows = connection.execute(
            "SELECT * FROM brain_items WHERE status = 'active' AND created_at >= ? "
            "ORDER BY CASE "
            "WHEN last_organized_at IS NULL AND summary <> '' AND summary <> title AND title <> content THEN 0 "
            "WHEN last_organized_at IS NULL THEN 1 "
            "WHEN summary = '' OR summary = title OR title = content THEN 2 ELSE 3 END, "
            "last_organized_at ASC, created_at ASC, id ASC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [brain_item_from_row(row) for row in rows]


def update_organized_metadata(
    item_id: int, analysis: "BrainAnalysis", organized_at: datetime
) -> BrainItem | None:
    with _brain_database() as connection:
        cursor = connection.execute(
            "UPDATE brain_items SET title = ?, summary = ?, item_type = CASE "
            "WHEN item_type = 'task' THEN item_type ELSE ? END, tags_json = ?, "
            "updated_at = ?, last_organized_at = ? WHERE id = ? AND status = 'active'",
            (
                analysis.title,
                analysis.summary,
                analysis.item_type,
                json.dumps(analysis.tags),
                format_timestamp(organized_at),
                format_timestamp(organized_at),
                item_id,
            ),
        )
        if cursor.rowcount == 0:
            return None
        row = connection.execute(
            "SELECT * FROM brain_items WHERE id = ? AND status = 'active'", (item_id,)
        ).fetchone()
    return brain_item_from_row(row) if row else None


def acquire_brain_organization_lock(now: datetime) -> str | None:
    token = str(uuid.uuid4())
    lease_expires_at = format_timestamp(now.astimezone(timezone.utc) + timedelta(hours=1))
    current_time = format_timestamp(now)
    with _brain_database() as connection:
        cursor = connection.execute(
            "INSERT INTO brain_organization_lock (id, lock_token, lease_expires_at) "
            "VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET lock_token = excluded.lock_token, "
            "lease_expires_at = excluded.lease_expires_at "
            "WHERE brain_organization_lock.lease_expires_at <= ?",
            (token, lease_expires_at, current_time),
        )
    return token if cursor.rowcount == 1 else None


def release_brain_organization_lock(token: str) -> None:
    with _brain_database() as connection:
        connection.execute(
            "DELETE FROM brain_organization_lock WHERE id = 1 AND lock_token = ?", (token,)
        )


def renew_brain_organization_lock(token: str, now: datetime) -> bool:
    lease_expires_at = format_timestamp(now.astimezone(timezone.utc) + timedelta(hours=1))
    with _brain_database() as connection:
        cursor = connection.execute(
            "UPDATE brain_organization_lock SET lease_expires_at = ? "
            "WHERE id = 1 AND lock_token = ?",
            (lease_expires_at, token),
        )
    return cursor.rowcount == 1


def list_unconnected_active_items() -> list[BrainItem]:
    with _brain_database() as connection:
        rows = connection.execute(
            "SELECT brain_items.* FROM brain_items WHERE status = 'active' AND NOT EXISTS ("
            "SELECT 1 FROM brain_item_relations WHERE source_item_id = brain_items.id "
            "OR target_item_id = brain_items.id) ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [brain_item_from_row(row) for row in rows]


def search_items(
    query: str, limit: int = 20, item_type: str | None = None
) -> list[BrainItem]:
    with _brain_database() as connection:
        terms = " OR ".join(f'"{term}"' for term in query.split() if term)
        rows = connection.execute(
            "SELECT brain_items.* FROM brain_items_fts "
            "JOIN brain_items ON brain_items.id = brain_items_fts.rowid "
            "WHERE brain_items_fts MATCH ? AND brain_items.status = 'active' "
            "AND (? IS NULL OR brain_items.item_type = ?) ORDER BY rank LIMIT ?",
            (terms, item_type, item_type, limit),
        ).fetchall() if terms else []
    return [brain_item_from_row(row) for row in rows]


def auto_capture_enabled() -> bool:
    with _brain_database() as connection:
        row = connection.execute(
            "SELECT auto_capture_enabled FROM brain_settings WHERE id = 1"
        ).fetchone()
    return bool(row["auto_capture_enabled"])


def set_auto_capture_enabled(enabled: bool) -> None:
    with _brain_database() as connection:
        connection.execute(
            "UPDATE brain_settings SET auto_capture_enabled = ?, updated_at = ? WHERE id = 1",
            (int(enabled), format_timestamp(utc_now())),
        )


def archive_item(item_id: int) -> bool:
    with _brain_database() as connection:
        cursor = connection.execute(
            "UPDATE brain_items SET status = 'archived', updated_at = ? "
            "WHERE id = ? AND status = 'active'",
            (format_timestamp(utc_now()), item_id),
        )
    return cursor.rowcount == 1


def list_item_deliveries(item_id: int) -> list[ReminderDelivery]:
    with _brain_database() as connection:
        rows = connection.execute("SELECT reminder_deliveries.*, brain_items.title FROM reminder_deliveries JOIN brain_items ON brain_items.id = reminder_deliveries.brain_item_id WHERE brain_item_id = ? ORDER BY reminder_deliveries.id", (item_id,)).fetchall()
    return [ReminderDelivery(row["id"], row["brain_item_id"], row["title"], parse_timestamp(row["scheduled_at"]), row["attempt_count"], row["lease_token"] or "") for row in rows]


def export_brain_data() -> dict[str, object]:
    with _brain_database() as connection:
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
    with _brain_database() as connection:
        connection.execute("DELETE FROM reminder_deliveries")
        connection.execute("DELETE FROM brain_item_relations")
        connection.execute("DELETE FROM brain_capture_items")
        connection.execute("DELETE FROM brain_captures")
        connection.execute("DELETE FROM brain_items")
        connection.execute("DELETE FROM brain_settings")


def delete_item(item_id: int) -> bool:
    with _brain_database() as connection:
        cursor = connection.execute("DELETE FROM brain_items WHERE id = ?", (item_id,))
        if cursor.rowcount:
            connection.execute("DELETE FROM reminder_deliveries WHERE brain_item_id = ?", (item_id,))
            connection.execute("DELETE FROM brain_capture_items WHERE brain_item_id = ?", (item_id,))
            connection.execute(
                "DELETE FROM brain_item_relations WHERE source_item_id = ? OR target_item_id = ?",
                (item_id, item_id),
            )
    return cursor.rowcount == 1


def delete_active_item_with_title(
    item_id: int, title: str
) -> Literal["deleted", "mismatch", "not_found"]:
    with _brain_database() as connection:
        cursor = connection.execute(
            "DELETE FROM brain_items WHERE id = ? AND title = ? AND status = 'active'",
            (item_id, title),
        )
        if cursor.rowcount:
            connection.execute("DELETE FROM reminder_deliveries WHERE brain_item_id = ?", (item_id,))
            connection.execute("DELETE FROM brain_capture_items WHERE brain_item_id = ?", (item_id,))
            connection.execute(
                "DELETE FROM brain_item_relations WHERE source_item_id = ? OR target_item_id = ?",
                (item_id, item_id),
            )
            return "deleted"
        item = connection.execute(
            "SELECT title, status FROM brain_items WHERE id = ?", (item_id,)
        ).fetchone()
    if item is None or item["status"] != "active":
        return "not_found"
    return "mismatch"
