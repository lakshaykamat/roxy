import io
import json
import logging
import re

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes
from zoneinfo import ZoneInfo

from src.knowledge import brain
from src.knowledge import service as privacy
from src.reminders import repository as tasks
from src.core.errors import log_async_error, try_catch

logger = logging.getLogger(__name__)

TASKS_BUTTON_TEXT = "📅 My tasks"
COMPLETION_CALLBACK_PATTERN = re.compile(r"done:(\d+)")


def task_list_response() -> str:
    active_tasks = tasks.list_active_tasks()
    if not active_tasks:
        return "You don't have any active tasks."

    lines = ["Your active tasks:"]
    for task in active_tasks:
        due_at = task.next_due_at.astimezone(ZoneInfo(task.timezone))
        recurrence = f" ({task.recurrence_rule})" if task.recurrence_rule else ""
        lines.append(
            f"{task.id}. {task.title} — {due_at:%d %b %Y, %I:%M %p} {task.timezone}{recurrence}"
        )
    lines.append("\nTo complete a task, use /done <task id>.")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm Roxy 👋 What's on your mind?",
        reply_markup=ReplyKeyboardMarkup(
            [[TASKS_BUTTON_TEXT]], resize_keyboard=True, is_persistent=True
        ),
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(task_list_response())


def completion_task_id(callback_data: object) -> int | None:
    if not isinstance(callback_data, str):
        return None
    match = COMPLETION_CALLBACK_PATTERN.fullmatch(callback_data)
    return int(match.group(1)) if match else None


def log_task_list_error(error: BaseException) -> None:
    logger.exception("Unable to load active tasks")


def log_task_completion_error(error: BaseException) -> None:
    logger.exception("Unable to complete scheduled task")


async def complete_task_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    callback_query = update.callback_query
    task_id = completion_task_id(callback_query.data)
    if task_id is None:
        await log_async_error(
            lambda: callback_query.answer("This task action is invalid."),
            logger=logger,
            error_message="Unable to acknowledge invalid task completion callback",
        )
        return

    completed = try_catch(
        lambda: tasks.complete_task(task_id),
        handle_error=log_task_completion_error,
    )
    if completed is None:
        await log_async_error(
            lambda: callback_query.answer("I couldn't update that task. Please try again."),
            logger=logger,
            error_message="Unable to acknowledge failed task completion callback",
        )
        return

    response = try_catch(task_list_response, handle_error=log_task_list_error)
    if response is None:
        refresh_failure_message = (
            "Task updated, but I couldn't refresh the task list."
            if completed
            else "This task is no longer active, but I couldn't refresh the task list."
        )
        await log_async_error(
            lambda: callback_query.answer(refresh_failure_message),
            logger=logger,
            error_message="Unable to acknowledge task list refresh failure",
        )
        return

    acknowledgement = (
        "Task marked complete."
        if completed
        else "This task is no longer active."
    )
    await log_async_error(
        lambda: callback_query.answer(acknowledgement),
        logger=logger,
        error_message="Unable to acknowledge task completion callback",
    )

    await log_async_error(
        lambda: callback_query.edit_message_text(response),
        logger=logger,
        error_message="Unable to refresh task list after completion",
    )


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Use /done <task id>, for example /done 3.")
        return

    task_id = int(context.args[0])
    if tasks.complete_task(task_id):
        await update.message.reply_text(f"Task {task_id} marked complete.")
    else:
        await update.message.reply_text(f"I couldn't find an active task with ID {task_id}.")


def brain_list_response() -> str:
    items = brain.list_recent_items()
    if not items:
        return "Your brain is empty."
    lines = ["Your newest brain items:"]
    for item in items:
        tags = f" ({', '.join(item.tags)})" if item.tags else ""
        lines.append(f"{item.id}. {item.title} [{item.item_type}]{tags}\n{item.summary}")
    return "\n".join(lines)


def brain_pause_response() -> str:
    brain.set_auto_capture_enabled(False)
    return "Automatic brain capture is paused."


def brain_resume_response() -> str:
    brain.set_auto_capture_enabled(True)
    return "Automatic brain capture is on."


def brain_item_response(arguments: list[str], *, delete: bool = False) -> str:
    if len(arguments) != 1 or not arguments[0].isdigit() or int(arguments[0]) <= 0:
        action = "delete" if delete else "archive"
        return f"Use /brain_{action} <brain item id>, for example /brain_{action} 3."
    item_id = int(arguments[0])
    changed = brain.delete_item(item_id) if delete else brain.archive_item(item_id)
    if changed:
        return "Brain item deleted." if delete else "Brain item archived."
    return "I couldn't find an active brain item with that ID."


async def list_brain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(brain_list_response())


async def brain_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(brain_pause_response())


async def brain_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(brain_resume_response())


async def brain_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(brain_item_response(context.args))


async def brain_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(brain_item_response(context.args, delete=True))


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payload = json.dumps(privacy.export_local_data(), ensure_ascii=False, indent=2)
    document = io.BytesIO(payload.encode("utf-8"))
    document.name = "roxy-data-export.json"
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=document,
        filename="roxy-data-export.json",
    )


def delete_data_response(arguments: list[str]) -> str:
    if arguments != ["CONFIRM"]:
        return "This deletes Roxy's local messages, brain items, and reminder deliveries. Use /delete_data CONFIRM to continue."
    privacy.delete_local_data()
    return "Roxy's local messages, brain items, and reminder deliveries have been deleted."


async def delete_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(delete_data_response(context.args))
