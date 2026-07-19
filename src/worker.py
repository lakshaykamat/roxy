import asyncio
import logging
import sqlite3
from datetime import timedelta

from telegram import Bot
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut

from src.config import ALLOWED_USER_ID, BOT_TOKEN
from src.utils import tasks

logger = logging.getLogger(__name__)


class ReminderWorker:
    def __init__(self, bot: Bot, poll_interval_seconds: int = 10):
        self.bot = bot
        self.poll_interval_seconds = poll_interval_seconds

    async def process_next_reminder(self) -> bool:
        try:
            reminder = tasks.claim_due_reminder()
            if reminder is None:
                return False

            try:
                await self.bot.send_message(
                    chat_id=ALLOWED_USER_ID,
                    text=f"Reminder: {reminder.title}",
                )
            except (NetworkError, RetryAfter, TimedOut, OSError) as error:
                retry_at = tasks.utc_now() + retry_delay(reminder.attempt_count)
                tasks.record_delivery_failure(
                    reminder.id, reminder.lease_token, str(error), retry_at
                )
                logger.warning(
                    "Reminder %s delivery failed and will retry: %s", reminder.id, error
                )
            except TelegramError as error:
                tasks.mark_reminder_failed(reminder.id, reminder.lease_token, str(error))
                logger.error("Reminder %s cannot be delivered: %s", reminder.id, error)
            except Exception as error:
                retry_at = tasks.utc_now() + retry_delay(reminder.attempt_count)
                tasks.record_delivery_failure(
                    reminder.id, reminder.lease_token, str(error), retry_at
                )
                logger.exception("Reminder %s delivery failed unexpectedly", reminder.id)
            else:
                tasks.mark_reminder_delivered(reminder.id, reminder.lease_token)
                logger.info("Delivered reminder %s", reminder.id)
            return True
        except sqlite3.Error:
            logger.exception("Unable to update reminder delivery state")
            return False

    async def run(self) -> None:
        logger.info("Reminder worker started")
        while True:
            processed_reminder = await self.process_next_reminder()
            if not processed_reminder:
                await asyncio.sleep(self.poll_interval_seconds)


def retry_delay(attempt_count: int) -> timedelta:
    return timedelta(seconds=min(60 * (2 ** (attempt_count - 1)), 3600))


async def run_worker() -> None:
    async with Bot(BOT_TOKEN) as bot:
        await ReminderWorker(bot).run()
