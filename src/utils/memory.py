import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from src import config
from src.utils import history, tasks
from src.utils.history import database_connection, format_timestamp

MEMORY_KINDS = {"fact", "person", "preference", "routine", "project"}


@dataclass(frozen=True)
class Memory:
    id: int
    kind: str
    content: str
    created_at: datetime
    expires_at: datetime | None


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS memories "
        "(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, "
        "created_at TEXT NOT NULL, expires_at TEXT)"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _expires_at(now: datetime) -> datetime | None:
    if config.MEMORY_RETENTION_DAYS == 0:
        return None
    return now + timedelta(days=config.MEMORY_RETENTION_DAYS)


def _memory_from_row(row: sqlite3.Row) -> Memory:
    expires_at = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
    return Memory(
        id=row["id"],
        kind=row["kind"],
        content=row["content"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=expires_at,
    )


def create_memory(content: str, kind: str = "fact") -> Memory:
    cleaned_content = content.strip() if isinstance(content, str) else ""
    if not cleaned_content:
        raise ValueError("Memory content is required.")
    if kind not in MEMORY_KINDS:
        raise ValueError("Memory kind is not supported.")

    created_at = _utc_now()
    expires_at = _expires_at(created_at)
    with database_connection() as connection:
        _initialize_schema(connection)
        cursor = connection.execute(
            "INSERT INTO memories (kind, content, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (kind, cleaned_content, format_timestamp(created_at), format_timestamp(expires_at) if expires_at else None),
        )
        row = connection.execute("SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _memory_from_row(row)


def list_memories() -> list[Memory]:
    with database_connection() as connection:
        _initialize_schema(connection)
        rows = connection.execute(
            "SELECT * FROM memories WHERE expires_at IS NULL OR expires_at > ? ORDER BY id",
            (format_timestamp(_utc_now()),),
        ).fetchall()
    return [_memory_from_row(row) for row in rows]


def _words(value: str) -> set[str]:
    return set(re.findall(r"[\w']+", value.lower()))


def find_relevant_memories(text: str, limit: int = 8) -> list[Memory]:
    query_words = _words(text)
    matches = [
        (len(query_words & _words(memory.content)), memory)
        for memory in list_memories()
    ]
    matches.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
    return [memory for score, memory in matches if score > 0][:limit]


def delete_memory(memory_id: int) -> bool:
    with database_connection() as connection:
        _initialize_schema(connection)
        cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return cursor.rowcount == 1


def _export_memory(memory: Memory) -> dict[str, object]:
    payload = asdict(memory)
    payload["created_at"] = memory.created_at.isoformat()
    payload["expires_at"] = memory.expires_at.isoformat() if memory.expires_at else None
    return payload


def export_local_data() -> dict[str, object]:
    with tasks.database_connection() as connection:
        task_rows = connection.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        reminder_rows = connection.execute("SELECT * FROM reminders ORDER BY id").fetchall()
    return {
        "exported_at": _utc_now().isoformat(),
        "messages": history.get(),
        "memories": [_export_memory(memory) for memory in list_memories()],
        "tasks": [dict(row) for row in task_rows],
        "reminders": [dict(row) for row in reminder_rows],
    }


def delete_local_data() -> None:
    table_names = {
        "messages",
        "memories",
        "reminders",
        "tasks",
    }
    with database_connection() as connection:
        _initialize_schema(connection)
        existing_tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table_name in table_names & existing_tables:
            connection.execute(f"DELETE FROM {table_name}")


def purge_expired_data(now: datetime | None = None) -> dict[str, int]:
    cutoff = format_timestamp(now or _utc_now())
    with database_connection() as connection:
        _initialize_schema(connection)
        messages = connection.execute(
            "DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at <= ?", (cutoff,)
        ).rowcount
        memories = connection.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?", (cutoff,)
        ).rowcount
    return {"messages": messages, "memories": memories}
