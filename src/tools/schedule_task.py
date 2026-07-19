import json
import logging
import sqlite3
from zoneinfo import ZoneInfo

from src.config import TASK_TIMEZONE
from src.utils import tasks

logger = logging.getLogger(__name__)

DEFINITION = {
    "type": "function",
    "function": {
        "name": "schedule_task",
        "description": "Create a one-time or recurring reminder for the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Concise reminder text."},
                "due_at": {
                    "type": "string",
                    "description": "Timezone-aware ISO 8601 datetime.",
                },
                "recurrence": {
                    "type": "string",
                    "description": "daily, weekly:<weekday>, or monthly:<day-of-month>.",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name; defaults to Asia/Kolkata.",
                },
            },
            "required": ["title", "due_at"],
            "additionalProperties": False,
        },
    },
}


def execute(arguments: str) -> dict[str, object]:
    try:
        values = json.loads(arguments)
        if not isinstance(values, dict):
            raise ValueError("Tool arguments must be an object.")
        if set(values) - {"title", "due_at", "recurrence", "timezone"}:
            raise ValueError("Tool arguments contain an unsupported field.")
        task = tasks.create_task(
            title=values["title"],
            due_at=values["due_at"],
            recurrence=values.get("recurrence"),
            task_timezone=values.get("timezone") or TASK_TIMEZONE,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return {"ok": False, "error": str(error)}
    except sqlite3.Error:
        logger.exception("Unable to create scheduled task")
        return {"ok": False, "error": "I couldn't save that reminder. Please try again."}

    due_at = task.next_due_at.astimezone(ZoneInfo(task.timezone))
    return {
        "ok": True,
        "task_id": task.id,
        "title": task.title,
        "due_at": due_at.isoformat(),
        "timezone": task.timezone,
        "recurrence": task.recurrence_rule or "one-time",
    }
