import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DATABASE_PATH = Path(__file__).resolve().parents[2] / "roxy.db"
MAX_MESSAGES = 40

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
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute(CREATE_MESSAGES_TABLE)
        yield connection
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        logger.exception("Unable to access conversation history database")
        raise
    finally:
        connection.close()


def add(role: str, content: str) -> None:
    with database_connection() as connection:
        connection.execute(
            "INSERT INTO messages (role, content) VALUES (?, ?)",
            (role, content),
        )


def get() -> list[dict[str, str]]:
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (MAX_MESSAGES,),
        ).fetchall()

    return [{"role": role, "content": content} for role, content in reversed(rows)]
