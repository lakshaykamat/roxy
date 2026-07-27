import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from src import config
from src.core.errors import try_catch_context

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT NOT NULL, "
        "content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, expires_at TEXT)"
    )


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(config.DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    def handle_database_error(error: BaseException) -> None:
        connection.rollback()
        logger.exception("Unable to access local database")
        raise error

    with try_catch_context(handle_error=handle_database_error, exception_types=sqlite3.Error, success_handler=connection.commit, finally_handler=connection.close):
        _initialize_schema(connection)
        yield connection


def _message_expiry() -> str | None:
    if config.HISTORY_RETENTION_DAYS <= 0:
        return None
    return format_timestamp(utc_now() + timedelta(days=config.HISTORY_RETENTION_DAYS))


def add(role: str, content: str) -> int:
    with database_connection() as connection:
        cursor = connection.execute("INSERT INTO messages (role, content, expires_at) VALUES (?, ?, ?)", (role, content, _message_expiry()))
    return cursor.lastrowid


def _messages_from_rows(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def get() -> list[dict[str, str]]:
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT role, content FROM messages "
            "WHERE expires_at IS NULL OR expires_at > ? "
            "ORDER BY id DESC LIMIT ?",
            (format_timestamp(utc_now()), config.MAX_MESSAGES),
        ).fetchall()
    return _messages_from_rows(rows)


def get_before(message_id: int) -> list[dict[str, str]]:
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT role, content FROM messages "
            "WHERE id < ? AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY id DESC LIMIT ?",
            (message_id, format_timestamp(utc_now()), config.MAX_MESSAGES),
        ).fetchall()
    return _messages_from_rows(rows)
