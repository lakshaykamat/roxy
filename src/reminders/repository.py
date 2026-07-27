import calendar
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src import config
from src.knowledge import brain
from src.core.errors import try_catch
from src.conversations.history import database_connection as history_database_connection

UNSET = object()


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


Reminder = brain.ReminderDelivery
utc_now = brain.utc_now
format_timestamp = brain.format_timestamp
parse_timestamp = brain.parse_timestamp


def task_from_item(item: brain.BrainItem) -> ScheduledTask:
    if item.due_at is None or item.timezone is None:
        raise ValueError("Task brain items require a due time and timezone.")
    return ScheduledTask(item.id, item.title, item.timezone, item.status, item.recurrence_rule, item.due_at, item.created_at, item.completed_at)


def database_connection():
    brain.initialize_schema()
    return history_database_connection()


def _cancel_pending_deliveries(connection, task_id: int, updated_at: str) -> None:
    connection.execute(
        "UPDATE reminder_deliveries "
        "SET status = 'failed', lease_expires_at = NULL, lease_token = NULL, updated_at = ? "
        "WHERE brain_item_id = ? AND status IN ('pending', 'leased')",
        (updated_at, task_id),
    )


def _schedule_delivery(connection, task_id: int, scheduled_at: datetime, created_at: str) -> None:
    connection.execute(
        "INSERT INTO reminder_deliveries "
        "(brain_item_id, scheduled_at, status, created_at, updated_at) "
        "VALUES (?, ?, 'pending', ?, ?)",
        (task_id, format_timestamp(scheduled_at), created_at, created_at),
    )


def validate_recurrence(recurrence: str | None) -> str | None:
    if recurrence is None or recurrence == "daily":
        return recurrence
    if not isinstance(recurrence, str):
        raise ValueError("Recurrence must be a string.")
    prefix, separator, value = recurrence.partition(":")
    if prefix == "weekly" and separator and value.lower() in {day.lower() for day in calendar.day_name}:
        return f"weekly:{value.lower()}"
    if prefix == "monthly" and separator and value.isdigit() and 1 <= int(value) <= 31:
        return f"monthly:{int(value)}"
    raise ValueError("Recurrence must be daily, weekly:<weekday>, or monthly:<day>.")


def validate_schedule(due_at: str, recurrence: str | None, task_timezone: str | None, *, now: datetime | None = None) -> tuple[datetime, str, str | None]:
    timezone_name = task_timezone or config.TASK_TIMEZONE
    def invalid_timezone(error: BaseException) -> None:
        raise ValueError("Timezone must be a valid IANA timezone name.") from error
    try_catch(lambda: ZoneInfo(timezone_name), handle_error=invalid_timezone, exception_types=(TypeError, ZoneInfoNotFoundError))
    def invalid_due_at(error: BaseException) -> None:
        raise ValueError("Due time must be an ISO 8601 datetime.") from error
    parsed_due_at = try_catch(lambda: datetime.fromisoformat(due_at), handle_error=invalid_due_at, exception_types=(TypeError, ValueError))
    if parsed_due_at.tzinfo is None or parsed_due_at.utcoffset() is None:
        raise ValueError("Due time must include a timezone offset.")
    due_at_utc = parsed_due_at.astimezone(timezone.utc)
    if due_at_utc <= (now or utc_now()).astimezone(timezone.utc):
        raise ValueError("Due time must be in the future.")
    return due_at_utc, timezone_name, validate_recurrence(recurrence)


def create_task(
    title: str,
    due_at: str,
    recurrence: str | None = None,
    task_timezone: str | None = None,
) -> ScheduledTask:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Task title is required.")
    due_time, timezone_name, recurrence_rule = validate_schedule(
        due_at, recurrence, task_timezone
    )
    task = brain.create_item(title, title, title, "task", [], "command", "explicit", due_at=due_time, timezone_name=timezone_name, recurrence_rule=recurrence_rule)
    now = format_timestamp(utc_now())
    with database_connection() as connection:
        _schedule_delivery(connection, task.id, due_time, now)
    return task_from_item(task)


def list_active_tasks() -> list[ScheduledTask]:
    brain.initialize_schema()
    with database_connection() as connection:
        rows = connection.execute("SELECT * FROM brain_items WHERE item_type = 'task' AND status = 'active' ORDER BY due_at, id").fetchall()
    return [task_from_item(brain.item_from_row(row)) for row in rows]

def complete_task(task_id: int) -> bool:
    now = format_timestamp(utc_now())
    with database_connection() as connection:
        cursor = connection.execute("UPDATE brain_items SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ? AND item_type = 'task' AND status = 'active'", (now, now, task_id))
        if cursor.rowcount:
            _cancel_pending_deliveries(connection, task_id, now)
    return cursor.rowcount == 1


def clear_active_tasks() -> int:
    now = format_timestamp(utc_now())
    with database_connection() as connection:
        connection.execute(
            "UPDATE reminder_deliveries "
            "SET status = 'failed', lease_expires_at = NULL, lease_token = NULL, updated_at = ? "
            "WHERE status IN ('pending', 'leased') AND brain_item_id IN "
            "(SELECT id FROM brain_items WHERE item_type = 'task' AND status = 'active')",
            (now,),
        )
        cursor = connection.execute("UPDATE brain_items SET status = 'cancelled', updated_at = ? WHERE item_type = 'task' AND status = 'active'", (now,))
    return cursor.rowcount


def complete_tasks(task_ids: list[int]) -> int:
    if not task_ids or any(not isinstance(task_id, int) or isinstance(task_id, bool) for task_id in task_ids):
        raise ValueError("Task IDs must be a non-empty list of whole numbers.")
    return sum(complete_task(task_id) for task_id in set(task_ids))


def update_task(task_id: int, *, title: str | None = None, due_at: str | None = None, recurrence: str | None | object = UNSET, task_timezone: str | object = UNSET) -> ScheduledTask | None:
    if not isinstance(task_id, int) or isinstance(task_id, bool):
        raise ValueError("Task ID must be a whole number.")
    with database_connection() as connection:
        row = connection.execute("SELECT * FROM brain_items WHERE id = ? AND item_type = 'task' AND status = 'active'", (task_id,)).fetchone()
        if row is None:
            return None
        new_title = title.strip() if title is not None else row["title"]
        if not new_title:
            raise ValueError("Task title is required.")
        new_timezone = row["timezone"] if task_timezone is UNSET else task_timezone
        new_recurrence = row["recurrence_rule"] if recurrence is UNSET else recurrence
        new_due_at, new_timezone, new_recurrence = validate_schedule(due_at or row["due_at"], new_recurrence, new_timezone)
        now = format_timestamp(utc_now())
        connection.execute("UPDATE brain_items SET title = ?, content = ?, summary = ?, due_at = ?, timezone = ?, recurrence_rule = ?, updated_at = ? WHERE id = ?", (new_title, new_title, new_title, format_timestamp(new_due_at), new_timezone, new_recurrence, now, task_id))
        if due_at is not None or recurrence is not UNSET or task_timezone is not UNSET:
            _cancel_pending_deliveries(connection, task_id, now)
            _schedule_delivery(connection, task_id, new_due_at, now)
        updated = connection.execute("SELECT * FROM brain_items WHERE id = ?", (task_id,)).fetchone()
    return task_from_item(brain.item_from_row(updated))


def claim_due_reminder(now: datetime | None = None) -> Reminder | None:
    claim_time = (now or utc_now()).astimezone(timezone.utc)
    token = str(uuid.uuid4())
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE reminder_deliveries SET status = 'pending', lease_expires_at = NULL, lease_token = NULL, updated_at = ? WHERE status = 'leased' AND lease_expires_at <= ?", (format_timestamp(claim_time), format_timestamp(claim_time)))
        row = connection.execute("SELECT reminder_deliveries.*, brain_items.title FROM reminder_deliveries JOIN brain_items ON brain_items.id = reminder_deliveries.brain_item_id WHERE reminder_deliveries.status = 'pending' AND reminder_deliveries.scheduled_at <= ? AND brain_items.status = 'active' ORDER BY reminder_deliveries.scheduled_at, reminder_deliveries.id LIMIT 1", (format_timestamp(claim_time),)).fetchone()
        if row is None:
            return None
        connection.execute("UPDATE reminder_deliveries SET status = 'leased', lease_expires_at = ?, attempt_count = attempt_count + 1, lease_token = ?, updated_at = ? WHERE id = ?", (format_timestamp(claim_time + config.LEASE_DURATION), token, format_timestamp(claim_time), row["id"]))
    return Reminder(row["id"], row["brain_item_id"], row["title"], parse_timestamp(row["scheduled_at"]), row["attempt_count"] + 1, token)


def mark_reminder_delivered(reminder_id: int, lease_token: str, delivered_at: datetime | None = None) -> None:
    completion = (delivered_at or utc_now()).astimezone(timezone.utc)
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        delivery = connection.execute("SELECT * FROM reminder_deliveries WHERE id = ? AND status = 'leased' AND lease_token = ?", (reminder_id, lease_token)).fetchone()
        if delivery is None:
            return
        task = connection.execute("SELECT * FROM brain_items WHERE id = ?", (delivery["brain_item_id"],)).fetchone()
        connection.execute("UPDATE reminder_deliveries SET status = 'delivered', lease_expires_at = NULL, lease_token = NULL, delivered_at = ?, updated_at = ? WHERE id = ?", (format_timestamp(completion), format_timestamp(completion), reminder_id))
        if task["status"] != "active":
            return
        if task["recurrence_rule"]:
            next_due_at = next_occurrence(parse_timestamp(delivery["scheduled_at"]), task["timezone"], task["recurrence_rule"])
            while next_due_at <= completion:
                next_due_at = next_occurrence(next_due_at, task["timezone"], task["recurrence_rule"])
            connection.execute("UPDATE brain_items SET due_at = ?, updated_at = ? WHERE id = ?", (format_timestamp(next_due_at), format_timestamp(completion), task["id"]))
            _schedule_delivery(
                connection,
                task["id"],
                next_due_at,
                format_timestamp(completion),
            )
        else:
            connection.execute("UPDATE brain_items SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?", (format_timestamp(completion), format_timestamp(completion), task["id"]))


def record_delivery_failure(reminder_id: int, lease_token: str, error: str, retry_at: datetime | None = None) -> None:
    now = utc_now()
    with database_connection() as connection:
        delivery = connection.execute("SELECT attempt_count FROM reminder_deliveries WHERE id = ? AND status = 'leased' AND lease_token = ?", (reminder_id, lease_token)).fetchone()
        if delivery is None:
            return
        if delivery["attempt_count"] >= config.MAX_DELIVERY_ATTEMPTS:
            connection.execute("UPDATE reminder_deliveries SET status = 'failed', lease_expires_at = NULL, lease_token = NULL, last_error = ?, updated_at = ? WHERE id = ?", (error, format_timestamp(now), reminder_id))
            return
        connection.execute("UPDATE reminder_deliveries SET lease_expires_at = ?, last_error = ?, updated_at = ? WHERE id = ?", (format_timestamp((retry_at or now).astimezone(timezone.utc)), error, format_timestamp(now), reminder_id))


def mark_reminder_failed(reminder_id: int, lease_token: str, error: str) -> None:
    with database_connection() as connection:
        connection.execute("UPDATE reminder_deliveries SET status = 'failed', lease_expires_at = NULL, lease_token = NULL, last_error = ?, updated_at = ? WHERE id = ? AND status = 'leased' AND lease_token = ?", (error, format_timestamp(utc_now()), reminder_id, lease_token))


def next_occurrence(scheduled_at: datetime, timezone_name: str, recurrence: str) -> datetime:
    local_due_at = scheduled_at.astimezone(ZoneInfo(timezone_name))
    if recurrence == "daily":
        return (local_due_at + timedelta(days=1)).astimezone(timezone.utc)
    if recurrence.startswith("weekly:"):
        weekday = list(calendar.day_name).index(recurrence.split(":", 1)[1].capitalize())
        return (local_due_at + timedelta(days=(weekday - local_due_at.weekday()) % 7 or 7)).astimezone(timezone.utc)
    year = local_due_at.year + (local_due_at.month == 12)
    month = 1 if local_due_at.month == 12 else local_due_at.month + 1
    return local_due_at.replace(year=year, month=month, day=min(int(recurrence.split(":", 1)[1]), calendar.monthrange(year, month)[1])).astimezone(timezone.utc)
