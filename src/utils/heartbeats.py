from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from src import config
from src.utils.errors import try_catch
from src.utils.database import read_only_database_connection
from src.utils.history import database_connection, format_timestamp

SERVICE_NAMES = {"bot", "worker"}


@dataclass(frozen=True)
class Heartbeat:
    service_name: str
    updated_at: datetime
    status: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS service_heartbeats "
        "(service_name TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
    )


def record_heartbeat(service_name: str, now: datetime | None = None) -> None:
    if service_name not in SERVICE_NAMES:
        raise ValueError("Unknown service name.")
    updated_at = (now or utc_now()).astimezone(timezone.utc)
    with database_connection() as connection:
        _initialize_schema(connection)
        connection.execute(
            "INSERT INTO service_heartbeats (service_name, updated_at) VALUES (?, ?) "
            "ON CONFLICT(service_name) DO UPDATE SET updated_at = excluded.updated_at",
            (service_name, format_timestamp(updated_at)),
        )


def get_heartbeats(now: datetime | None = None) -> dict[str, Heartbeat]:
    current_time = (now or utc_now()).astimezone(timezone.utc)

    def load_rows() -> list[sqlite3.Row]:
        with read_only_database_connection() as connection:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'service_heartbeats'"
            ).fetchone()
            if table_exists is None:
                return []
            return connection.execute(
                "SELECT service_name, updated_at FROM service_heartbeats"
            ).fetchall()

    rows = try_catch(
        load_rows,
        handle_error=lambda _: [],
        exception_types=sqlite3.Error,
    )
    result: dict[str, Heartbeat] = {}
    for row in rows:
        updated_at = datetime.fromisoformat(row["updated_at"]).astimezone(timezone.utc)
        elapsed_seconds = (current_time - updated_at).total_seconds()
        result[row["service_name"]] = Heartbeat(
            service_name=row["service_name"],
            updated_at=updated_at,
            status=(
                "healthy"
                if elapsed_seconds <= config.HEARTBEAT_STALE_AFTER_SECONDS
                else "unhealthy"
            ),
        )
    return result
