import calendar
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src import config
from src.utils.errors import try_catch, try_catch_context

logger = logging.getLogger(__name__)

CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'cancelled')),
    recurrence_rule TEXT,
    next_due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
)
"""

CREATE_REMINDERS_TABLE = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'leased', 'delivered', 'failed')),
    lease_expires_at TEXT,
    lease_token TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

CREATE_REMINDER_LOOKUP_INDEX = """
CREATE INDEX IF NOT EXISTS reminders_due_index
ON reminders(status, scheduled_at)
"""


@dataclass(frozen=True)
class ScheduledTask:
    id: int
    title: str
    timezone: str
    status: str
    recurrence_rule: str | None
    next_due_at: datetime
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class Reminder:
    id: int
    task_id: int
    title: str
    scheduled_at: datetime
    attempt_count: int
    lease_token: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(config.DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    def handle_database_error(error: BaseException) -> None:
        connection.rollback()
        logger.exception("Unable to access scheduled tasks database")
        raise error

    with try_catch_context(
        handle_error=handle_database_error,
        exception_types=sqlite3.Error,
        success_handler=connection.commit,
        finally_handler=connection.close,
    ):
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(CREATE_TASKS_TABLE)
        connection.execute(CREATE_REMINDERS_TABLE)
        ensure_reminder_lease_token(connection)
        connection.execute(CREATE_REMINDER_LOOKUP_INDEX)
        yield connection


def ensure_reminder_lease_token(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(reminders)")}
    if "lease_token" not in columns:
        connection.execute("ALTER TABLE reminders ADD COLUMN lease_token TEXT")


def validate_recurrence(recurrence: str | None) -> str | None:
    if recurrence is None:
        return None
    if not isinstance(recurrence, str):
        raise ValueError("Recurrence must be a string.")
    if recurrence == "daily":
        return recurrence

    prefix, separator, value = recurrence.partition(":")
    if not separator:
        raise ValueError("Recurrence must be daily, weekly:<weekday>, or monthly:<day>.")
    if prefix == "weekly" and value.lower() in {day.lower() for day in calendar.day_name}:
        return f"weekly:{value.lower()}"
    if prefix == "monthly" and value.isdigit() and 1 <= int(value) <= 31:
        return f"monthly:{int(value)}"
    raise ValueError("Recurrence must be daily, weekly:<weekday>, or monthly:<day>.")


def validate_schedule(
    due_at: str,
    recurrence: str | None,
    task_timezone: str | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, str, str | None]:
    timezone_name = task_timezone or config.TASK_TIMEZONE
    if not isinstance(timezone_name, str):
        raise ValueError("Timezone must be a valid IANA timezone name.")
    def invalid_timezone(error: BaseException) -> None:
        raise ValueError("Timezone must be a valid IANA timezone name.") from error

    try_catch(
        lambda: ZoneInfo(timezone_name),
        handle_error=invalid_timezone,
        exception_types=(TypeError, ZoneInfoNotFoundError),
    )

    def invalid_due_at(error: BaseException) -> None:
        raise ValueError("Due time must be an ISO 8601 datetime.") from error

    parsed_due_at = try_catch(
        lambda: datetime.fromisoformat(due_at),
        handle_error=invalid_due_at,
        exception_types=(TypeError, ValueError),
    )
    if parsed_due_at.tzinfo is None or parsed_due_at.utcoffset() is None:
        raise ValueError("Due time must include a timezone offset.")

    due_at_utc = parsed_due_at.astimezone(timezone.utc)
    current_time = now or utc_now()
    if due_at_utc <= current_time.astimezone(timezone.utc):
        raise ValueError("Due time must be in the future.")
    return due_at_utc, timezone_name, validate_recurrence(recurrence)


def create_task(
    title: str,
    due_at: str,
    recurrence: str | None = None,
    task_timezone: str | None = None,
) -> ScheduledTask:
    if not isinstance(title, str):
        raise ValueError("Task title must be text.")
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Task title is required.")

    due_at_utc, timezone_name, recurrence_rule = validate_schedule(
        due_at, recurrence, task_timezone
    )
    created_at = utc_now()
    with database_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tasks (title, timezone, status, recurrence_rule, next_due_at, created_at)
            VALUES (?, ?, 'active', ?, ?, ?)
            """,
            (
                clean_title,
                timezone_name,
                recurrence_rule,
                format_timestamp(due_at_utc),
                format_timestamp(created_at),
            ),
        )
        task_id = cursor.lastrowid
        connection.execute(
            """
            INSERT INTO reminders (task_id, scheduled_at, status, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (
                task_id,
                format_timestamp(due_at_utc),
                format_timestamp(created_at),
                format_timestamp(created_at),
            ),
        )
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return task_from_row(row)


def list_active_tasks() -> list[ScheduledTask]:
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM tasks WHERE status = 'active' ORDER BY next_due_at, id"
        ).fetchall()
    return [task_from_row(row) for row in rows]


def complete_task(task_id: int) -> bool:
    completed_at = utc_now()
    with database_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE tasks SET status = 'completed', completed_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (format_timestamp(completed_at), task_id),
        )
        if cursor.rowcount:
            connection.execute(
                """
                UPDATE reminders SET status = 'failed', updated_at = ?
                WHERE task_id = ? AND status IN ('pending', 'leased')
                """,
                (format_timestamp(completed_at), task_id),
            )
        return cursor.rowcount == 1


def claim_due_reminder(now: datetime | None = None) -> Reminder | None:
    claim_time = (now or utc_now()).astimezone(timezone.utc)
    lease_expires_at = claim_time + config.LEASE_DURATION
    lease_token = str(uuid.uuid4())
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE reminders SET status = 'pending', lease_expires_at = NULL, updated_at = ?
            WHERE status = 'leased' AND lease_expires_at <= ?
            """,
            (format_timestamp(claim_time), format_timestamp(claim_time)),
        )
        row = connection.execute(
            """
            SELECT reminders.id, reminders.task_id, tasks.title, reminders.scheduled_at,
                   reminders.attempt_count
            FROM reminders JOIN tasks ON tasks.id = reminders.task_id
            WHERE reminders.status = 'pending' AND reminders.scheduled_at <= ?
              AND tasks.status = 'active'
            ORDER BY reminders.scheduled_at, reminders.id
            LIMIT 1
            """,
            (format_timestamp(claim_time),),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """
            UPDATE reminders
            SET status = 'leased', lease_expires_at = ?, attempt_count = attempt_count + 1,
                lease_token = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                format_timestamp(lease_expires_at),
                lease_token,
                format_timestamp(claim_time),
                row["id"],
            ),
        )
    return Reminder(
        id=row["id"],
        task_id=row["task_id"],
        title=row["title"],
        scheduled_at=parse_timestamp(row["scheduled_at"]),
        attempt_count=row["attempt_count"] + 1,
        lease_token=lease_token,
    )


def mark_reminder_delivered(
    reminder_id: int, lease_token: str, delivered_at: datetime | None = None
) -> None:
    completion_time = (delivered_at or utc_now()).astimezone(timezone.utc)
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        reminder = connection.execute(
            """SELECT * FROM reminders
            WHERE id = ? AND status = 'leased' AND lease_token = ?""",
            (reminder_id, lease_token),
        ).fetchone()
        if reminder is None:
            return
        task = connection.execute("SELECT * FROM tasks WHERE id = ?", (reminder["task_id"],)).fetchone()
        connection.execute(
            """
            UPDATE reminders
            SET status = 'delivered', lease_expires_at = NULL, lease_token = NULL,
                delivered_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (format_timestamp(completion_time), format_timestamp(completion_time), reminder_id),
        )
        if task["status"] == "active" and task["recurrence_rule"]:
            next_due_at = next_occurrence(
                parse_timestamp(reminder["scheduled_at"]),
                task["timezone"],
                task["recurrence_rule"],
            )
            while next_due_at <= completion_time:
                next_due_at = next_occurrence(
                    next_due_at,
                    task["timezone"],
                    task["recurrence_rule"],
                )
            connection.execute(
                "UPDATE tasks SET next_due_at = ? WHERE id = ?",
                (format_timestamp(next_due_at), task["id"]),
            )
            connection.execute(
                """
                INSERT INTO reminders (task_id, scheduled_at, status, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (
                    task["id"],
                    format_timestamp(next_due_at),
                    format_timestamp(completion_time),
                    format_timestamp(completion_time),
                ),
            )


def record_delivery_failure(
    reminder_id: int, lease_token: str, error: str, retry_at: datetime | None = None
) -> None:
    failure_time = utc_now()
    with database_connection() as connection:
        reminder = connection.execute(
            """SELECT attempt_count FROM reminders
            WHERE id = ? AND status = 'leased' AND lease_token = ?""",
            (reminder_id, lease_token),
        ).fetchone()
        if reminder is None:
            return
        if reminder["attempt_count"] >= config.MAX_DELIVERY_ATTEMPTS:
            connection.execute(
                """
                UPDATE reminders
                SET status = 'failed', lease_expires_at = NULL, lease_token = NULL,
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (error, format_timestamp(failure_time), reminder_id),
            )
            return
        next_attempt = (retry_at or failure_time).astimezone(timezone.utc)
        connection.execute(
            """
            UPDATE reminders
            SET lease_expires_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                format_timestamp(next_attempt),
                error,
                format_timestamp(failure_time),
                reminder_id,
            ),
        )


def mark_reminder_failed(reminder_id: int, lease_token: str, error: str) -> None:
    failure_time = utc_now()
    with database_connection() as connection:
        connection.execute(
            """
            UPDATE reminders
            SET status = 'failed', lease_expires_at = NULL, lease_token = NULL,
                last_error = ?, updated_at = ?
            WHERE id = ? AND status = 'leased' AND lease_token = ?
            """,
            (error, format_timestamp(failure_time), reminder_id, lease_token),
        )


def next_occurrence(scheduled_at: datetime, timezone_name: str, recurrence: str) -> datetime:
    local_due_at = scheduled_at.astimezone(ZoneInfo(timezone_name))
    if recurrence == "daily":
        return (local_due_at + timedelta(days=1)).astimezone(timezone.utc)
    if recurrence.startswith("weekly:"):
        weekday = list(calendar.day_name).index(recurrence.split(":", 1)[1].capitalize())
        days_until_next = (weekday - local_due_at.weekday()) % 7 or 7
        return (local_due_at + timedelta(days=days_until_next)).astimezone(timezone.utc)

    day_of_month = int(recurrence.split(":", 1)[1])
    year = local_due_at.year + (local_due_at.month == 12)
    month = 1 if local_due_at.month == 12 else local_due_at.month + 1
    day = min(day_of_month, calendar.monthrange(year, month)[1])
    return local_due_at.replace(year=year, month=month, day=day).astimezone(timezone.utc)


def task_from_row(row: sqlite3.Row) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        title=row["title"],
        timezone=row["timezone"],
        status=row["status"],
        recurrence_rule=row["recurrence_rule"],
        next_due_at=parse_timestamp(row["next_due_at"]),
        created_at=parse_timestamp(row["created_at"]),
        completed_at=parse_timestamp(row["completed_at"]) if row["completed_at"] else None,
    )
