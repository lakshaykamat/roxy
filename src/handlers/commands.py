import io
import json
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes
from zoneinfo import ZoneInfo

from src.knowledge import brain_store
from src.knowledge import data_management as privacy
from src.reminders import repository as tasks
from src.core.errors import log_async_error, try_catch

logger = logging.getLogger(__name__)

TASKS_BUTTON_TEXT = "📅 My tasks"
BRAIN_BUTTON_TEXT = "🧠 My brain"
EXPORT_DATA_BUTTON_TEXT = "📦 Export my data"
DELETE_DATA_BUTTON_TEXT = "🗑 Delete my data"
HELP_BUTTON_TEXT = "ℹ️ Help"
CONFIRM_DELETE_BUTTON_TEXT = "🗑 Delete permanently"
CANCEL_DELETE_BUTTON_TEXT = "Cancel"
COMPLETION_CALLBACK_PATTERN = re.compile(r"done:(\d+)")


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [TASKS_BUTTON_TEXT, BRAIN_BUTTON_TEXT],
            [EXPORT_DATA_BUTTON_TEXT, DELETE_DATA_BUTTON_TEXT],
            [HELP_BUTTON_TEXT],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def delete_confirmation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[CONFIRM_DELETE_BUTTON_TEXT, CANCEL_DELETE_BUTTON_TEXT]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def task_list_response(active_tasks: list[object]) -> str:
    if not active_tasks:
        return "You don't have any active tasks."

    lines = ["Your active tasks:"]
    for task in active_tasks:
        due_at = task.next_due_at.astimezone(ZoneInfo(task.timezone))
        recurrence = f" ({task.recurrence_rule})" if task.recurrence_rule else ""
        lines.append(
            f"{task.id}. {task.title} — {due_at:%d %b %Y, %I:%M %p} {task.timezone}{recurrence}"
        )
    lines.append("\nUse the Done buttons below to complete tasks.")
    return "\n".join(lines)


def task_list_keyboard(active_tasks: list[object]) -> InlineKeyboardMarkup | None:
    if not active_tasks:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"Done: {task.title}", callback_data=f"done:{task.id}")]
         for task in active_tasks]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm Roxy 👋 What's on your mind?",
        reply_markup=main_keyboard(),
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_tasks = tasks.list_active_tasks()
    await update.message.reply_text(
        task_list_response(active_tasks), reply_markup=task_list_keyboard(active_tasks)
    )


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

    task_view = try_catch(
        lambda: (
            (active_tasks := tasks.list_active_tasks()),
            task_list_response(active_tasks),
            task_list_keyboard(active_tasks),
        ),
        handle_error=log_task_list_error,
    )
    if task_view is None:
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
        lambda: callback_query.edit_message_text(task_view[1], reply_markup=task_view[2]),
        logger=logger,
        error_message="Unable to refresh task list after completion",
    )


def brain_list_response() -> str:
    items = brain_store.list_recent_items()
    if not items:
        return "Your brain is empty."
    lines = ["Your newest brain items:"]
    for item in items:
        tags = f" ({', '.join(item.tags)})" if item.tags else ""
        lines.append(f"{item.id}. {item.title} [{item.item_type}]{tags}\n{item.summary}")
    return "\n".join(lines)


async def list_brain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(brain_list_response())


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payload = json.dumps(privacy.export_user_data(), ensure_ascii=False, indent=2)
    document = io.BytesIO(payload.encode("utf-8"))
    document.name = "roxy-data-export.json"
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=document,
        filename="roxy-data-export.json",
    )


async def request_data_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["confirming_data_deletion"] = True
    await update.message.reply_text(
        "This permanently deletes Roxy's local messages, brain items, and reminder deliveries.",
        reply_markup=delete_confirmation_keyboard(),
    )


async def confirm_data_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.pop("confirming_data_deletion", False):
        await update.message.reply_text(
            "Choose Delete my data first.", reply_markup=main_keyboard()
        )
        return
    privacy.delete_user_data()
    await update.message.reply_text(
        "Roxy's local messages, brain items, and reminder deliveries have been deleted.",
        reply_markup=main_keyboard(),
    )


async def cancel_data_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("confirming_data_deletion", None)
    await update.message.reply_text("Deletion cancelled.", reply_markup=main_keyboard())


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Use the keyboard to view tasks and saved brain items, "
        "export your data, or permanently delete local data."
    )
