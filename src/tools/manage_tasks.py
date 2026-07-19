import json
import logging
import sqlite3
from zoneinfo import ZoneInfo

from src.utils import tasks
from src.utils.errors import try_catch

logger = logging.getLogger(__name__)

DEFINITION = {
    "type": "function",
    "function": {
        "name": "manage_reminders",
        "description": "Manage Roxy's local reminders: list, remove selected reminders, update one reminder, or clear all after explicit confirmation.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "remove", "update", "clear"],
                    "description": "The reminder management action.",
                },
                "task_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "description": "Reminder IDs to remove when action is remove.",
                },
                "task_id": {"type": "integer", "description": "Reminder ID to update."},
                "title": {"type": "string", "description": "New reminder text."},
                "due_at": {"type": "string", "description": "New timezone-aware ISO 8601 datetime."},
                "recurrence": {
                    "type": ["string", "null"],
                    "description": "daily, weekly:<weekday>, monthly:<day-of-month>, or null for one-time.",
                },
                "timezone": {"type": "string", "description": "New IANA timezone name."},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}


def execute(arguments: str) -> dict[str, object]:
    def manage() -> dict[str, object]:
        values = json.loads(arguments)
        if not isinstance(values, dict) or not isinstance(values.get("action"), str):
            raise ValueError("A reminder action is required.")
        action = values["action"]
        if action == "list":
            return list_tasks(values)
        if action == "remove":
            return remove_requested_tasks(values)
        if action == "update":
            return update_requested_task(values)
        if action == "clear":
            return clear_all_tasks(values)
        raise ValueError("That reminder action is not available.")

    return run_operation(manage)


def list_tasks(values: dict[str, object]) -> dict[str, object]:
    if set(values) != {"action"}:
        raise ValueError("Listing reminders does not accept other fields.")
    return {
        "ok": True,
        "tasks": [
            {
                "task_id": task.id,
                "title": task.title,
                "due_at": task.next_due_at.astimezone(ZoneInfo(task.timezone)).isoformat(),
                "timezone": task.timezone,
                "recurrence": task.recurrence_rule or "one-time",
            }
            for task in tasks.list_active_tasks()
        ],
    }


def remove_requested_tasks(values: dict[str, object]) -> dict[str, object]:
    if set(values) != {"action", "task_ids"}:
        raise ValueError("Task IDs are required to remove reminders.")
    return {"ok": True, "removed_count": tasks.complete_tasks(values["task_ids"])}


def update_requested_task(values: dict[str, object]) -> dict[str, object]:
    allowed_fields = {"action", "task_id", "title", "due_at", "recurrence", "timezone"}
    if "task_id" not in values or set(values) - allowed_fields:
        raise ValueError("A task ID and valid update fields are required.")
    update_values = {
        key: value for key, value in values.items() if key not in {"action", "task_id"}
    }
    if not update_values:
        raise ValueError("Specify at least one reminder change.")
    task = tasks.update_task(
        values["task_id"],
        title=update_values.get("title"),
        due_at=update_values.get("due_at"),
        recurrence=update_values.get("recurrence", tasks.UNSET),
        task_timezone=update_values.get("timezone", tasks.UNSET),
    )
    if task is None:
        return {"ok": False, "error": "I couldn't find that active reminder."}
    due_at = task.next_due_at.astimezone(ZoneInfo(task.timezone))
    return {
        "ok": True,
        "task_id": task.id,
        "title": task.title,
        "due_at": due_at.isoformat(),
        "timezone": task.timezone,
        "recurrence": task.recurrence_rule or "one-time",
    }


def clear_all_tasks(values: dict[str, object]) -> dict[str, object]:
    if set(values) != {"action"}:
        raise ValueError("Clearing reminders does not accept other fields.")
    return {"ok": True, "cleared_count": tasks.clear_active_tasks()}


def run_operation(operation):
    def handle_error(error: BaseException) -> dict[str, object]:
        if isinstance(error, sqlite3.Error):
            logger.exception("Unable to manage active reminders")
            return {"ok": False, "error": "I couldn't update your reminders. Please try again."}
        return {"ok": False, "error": str(error)}

    return try_catch(
        operation,
        handle_error=handle_error,
        exception_types=(TypeError, ValueError, json.JSONDecodeError, sqlite3.Error),
    )
