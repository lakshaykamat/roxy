import asyncio
import logging
import sqlite3
from datetime import timedelta

from telegram import Bot
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut

from src.config import ALLOWED_USER_ID, BOT_TOKEN
from src.prompts.system import SYSTEM_PROMPT
from src.utils.errors import try_async
from src.utils import tasks
from src.utils.llm import ask_llm

logger = logging.getLogger(__name__)


class ReminderWorker:
    def __init__(self, bot: Bot, poll_interval_seconds: int = 10):
        self.bot = bot
        self.poll_interval_seconds = poll_interval_seconds

    async def generate_reminder_message(self, reminder: tasks.Reminder) -> str:
        async def create_message() -> str:
            response = await ask_llm(
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": (
                            "Write one short, natural Telegram reminder. Return only the message. "
                            f"Reminder instruction: {reminder.title}"
                        ),
                    },
                ]
            )
            return response.choices[0].message.content.strip() or reminder.title

        async def use_title_after_generation_error(_: BaseException) -> str:
            logger.exception("Unable to generate reminder %s; sending its title", reminder.id)
            return reminder.title

        return await try_async(create_message, handle_error=use_title_after_generation_error)

    async def process_next_reminder(self) -> bool:
        async def process_reminder() -> bool:
            reminder = tasks.claim_due_reminder()
            if reminder is None:
                return False

            async def deliver_reminder() -> bool:
                message = await self.generate_reminder_message(reminder)
                await self.bot.send_message(
                    chat_id=ALLOWED_USER_ID,
                    text=message,
                )

                return True

            async def handle_delivery_error(error: BaseException) -> bool:
                if isinstance(error, (NetworkError, RetryAfter, TimedOut, OSError)):
                    retry_at = tasks.utc_now() + retry_delay(reminder.attempt_count)
                    tasks.record_delivery_failure(
                        reminder.id, reminder.lease_token, str(error), retry_at
                    )
                    logger.warning(
                        "Reminder %s delivery failed and will retry: %s", reminder.id, error
                    )
                elif isinstance(error, TelegramError):
                    tasks.mark_reminder_failed(
                        reminder.id, reminder.lease_token, str(error)
                    )
                    logger.error("Reminder %s cannot be delivered: %s", reminder.id, error)
                else:
                    retry_at = tasks.utc_now() + retry_delay(reminder.attempt_count)
                    tasks.record_delivery_failure(
                        reminder.id, reminder.lease_token, str(error), retry_at
                    )
                    logger.exception(
                        "Reminder %s delivery failed unexpectedly", reminder.id
                    )
                return False

            delivered = await try_async(
                deliver_reminder,
                handle_error=handle_delivery_error,
            )
            if delivered:
                tasks.mark_reminder_delivered(reminder.id, reminder.lease_token)
                logger.info("Delivered reminder %s", reminder.id)
            return True

        async def handle_database_error(_: BaseException) -> bool:
            logger.exception("Unable to update reminder delivery state")
            return False

        return await try_async(
            process_reminder,
            handle_error=handle_database_error,
            exception_types=sqlite3.Error,
        )

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
