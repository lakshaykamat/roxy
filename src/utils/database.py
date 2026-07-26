import sqlite3
from contextlib import contextmanager
from typing import Iterator

from src import config
from src.utils.errors import try_catch_context


@contextmanager
def read_only_database_connection() -> Iterator[sqlite3.Connection]:
    database_uri = f"{config.DATABASE_PATH.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(database_uri, uri=True)
    connection.row_factory = sqlite3.Row
    with try_catch_context(finally_handler=connection.close):
        yield connection
