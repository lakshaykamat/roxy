import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes
from zoneinfo import ZoneInfo

from src.utils import tasks
from src.utils.errors import log_async_error, try_catch

logger = logging.getLogger(__name__)

TASKS_BUTTON_TEXT = "📅 My tasks"
COMPLETION_CALLBACK_PATTERN = re.compile(r"done:(\d+)")


def task_list_response() -> tuple[str, InlineKeyboardMarkup | None]:
    active_tasks = tasks.list_active_tasks()
    if not active_tasks:
        return "You don't have any active tasks.", None

    lines = ["Your active tasks:"]
    buttons = []
    for task in active_tasks:
        due_at = task.next_due_at.astimezone(ZoneInfo(task.timezone))
        recurrence = f" ({task.recurrence_rule})" if task.recurrence_rule else ""
        lines.append(
            f"{task.id}. {task.title} — {due_at:%d %b %Y, %I:%M %p} {task.timezone}{recurrence}"
        )
        buttons.append(
            [InlineKeyboardButton("✅ Done", callback_data=f"done:{task.id}")]
        )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm Roxy 👋 What's on your mind?",
        reply_markup=ReplyKeyboardMarkup(
            [[TASKS_BUTTON_TEXT]], resize_keyboard=True, is_persistent=True
        ),
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, reply_markup = task_list_response()
    await update.message.reply_text(text, reply_markup=reply_markup)


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

    text, reply_markup = response
    await log_async_error(
        lambda: callback_query.edit_message_text(text, reply_markup=reply_markup),
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
