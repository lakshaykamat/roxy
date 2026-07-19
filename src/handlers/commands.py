from telegram import Update
from telegram.ext import ContextTypes
from zoneinfo import ZoneInfo

from src.utils import tasks


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey! I'm Roxy 👋 What's on your mind?")


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_tasks = tasks.list_active_tasks()
    if not active_tasks:
        await update.message.reply_text("You don't have any active tasks.")
        return

    lines = ["Your active tasks:"]
    for task in active_tasks:
        due_at = task.next_due_at.astimezone(ZoneInfo(task.timezone))
        recurrence = f" ({task.recurrence_rule})" if task.recurrence_rule else ""
        lines.append(
            f"{task.id}. {task.title} — {due_at:%d %b %Y, %I:%M %p} {task.timezone}{recurrence}"
        )
    await update.message.reply_text("\n".join(lines))


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Use /done <task id>, for example /done 3.")
        return

    task_id = int(context.args[0])
    if tasks.complete_task(task_id):
        await update.message.reply_text(f"Task {task_id} marked complete.")
    else:
        await update.message.reply_text(f"I couldn't find an active task with ID {task_id}.")
