import logging
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from src import config
from src.utils.errors import try_catch_context

logger = logging.getLogger(__name__)

CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(config.DATABASE_PATH)
    def handle_database_error(error: BaseException) -> None:
        connection.rollback()
        logger.exception("Unable to access conversation history database")
        raise error

    with try_catch_context(
        handle_error=handle_database_error,
        exception_types=sqlite3.Error,
        success_handler=connection.commit,
        finally_handler=connection.close,
    ):
        connection.execute(CREATE_MESSAGES_TABLE)
        yield connection


def add(role: str, content: str) -> int:
    with database_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO messages (role, content) VALUES (?, ?)",
            (role, content),
        )
    return cursor.lastrowid


def _messages_from_rows(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    return [{"role": role, "content": content} for role, content in reversed(rows)]


def get() -> list[dict[str, str]]:
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (config.MAX_MESSAGES,),
        ).fetchall()

    return _messages_from_rows(rows)


def get_before(message_id: int) -> list[dict[str, str]]:
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT role, content FROM messages WHERE id < ? ORDER BY id DESC LIMIT ?",
            (message_id, config.MAX_MESSAGES),
        ).fetchall()

    return _messages_from_rows(rows)
