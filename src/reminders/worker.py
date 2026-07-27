import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.request import HTTPXRequest

from src.config import (
    ALLOWED_USER_ID,
    BOT_TOKEN,
    TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    TELEGRAM_POOL_TIMEOUT_SECONDS,
    TELEGRAM_READ_TIMEOUT_SECONDS,
    TELEGRAM_WRITE_TIMEOUT_SECONDS,
    TASK_TIMEZONE,
)
from src.prompts.system import SYSTEM_PROMPT
from src.core.errors import try_async
from src.knowledge.introspection import next_introspection_at, refresh_brain_connections
from src.reminders import repository
from src.core.llm import ask_llm

logger = logging.getLogger(__name__)

WORKER_STARTUP_RETRY_DELAY_SECONDS = 5
MAX_WORKER_STARTUP_RETRY_DELAY_SECONDS = 60


class ReminderWorker:
    def __init__(self, bot: Bot, poll_interval_seconds: int = 10):
        self.bot = bot
        self.poll_interval_seconds = poll_interval_seconds
        self._last_introspection_date: str | None = None

    async def run_introspection_if_due(self, now: datetime | None = None) -> bool:
        current_time = now or repository.utc_now()
        local_time = current_time.astimezone(ZoneInfo(TASK_TIMEZONE))
        if local_time.hour < 3 or self._last_introspection_date == local_time.date().isoformat():
            return False

        async def refresh() -> bool:
            await refresh_brain_connections(current_time)
            self._last_introspection_date = local_time.date().isoformat()
            return True

        async def log_failure(_: BaseException) -> bool:
            logger.exception("Unable to refresh Brain connections")
            return False

        return await try_async(refresh, handle_error=log_failure)

    async def generate_reminder_message(self, reminder: repository.Reminder) -> str:
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
            reminder = repository.claim_due_reminder()
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
                    retry_at = repository.utc_now() + retry_delay(reminder.attempt_count)
                    repository.record_delivery_failure(
                        reminder.id, reminder.lease_token, str(error), retry_at
                    )
                    logger.warning(
                        "Reminder %s delivery failed and will retry: %s", reminder.id, error
                    )
                elif isinstance(error, TelegramError):
                    repository.mark_reminder_failed(
                        reminder.id, reminder.lease_token, str(error)
                    )
                    logger.error("Reminder %s cannot be delivered: %s", reminder.id, error)
                else:
                    retry_at = repository.utc_now() + retry_delay(reminder.attempt_count)
                    repository.record_delivery_failure(
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
                repository.mark_reminder_delivered(reminder.id, reminder.lease_token)
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
        scheduled_introspection = next_introspection_at(repository.utc_now(), TASK_TIMEZONE)
        while True:
            now = repository.utc_now()
            if now >= scheduled_introspection:
                asyncio.create_task(self.run_introspection_if_due(now))
                scheduled_introspection = next_introspection_at(now, TASK_TIMEZONE)
            processed_reminder = await self.process_next_reminder()
            if not processed_reminder:
                await asyncio.sleep(self.poll_interval_seconds)


def retry_delay(attempt_count: int) -> timedelta:
    return timedelta(seconds=min(60 * (2 ** (attempt_count - 1)), 3600))


def startup_retry_delay(attempt_count: int) -> int:
    return min(
        WORKER_STARTUP_RETRY_DELAY_SECONDS * (2 ** (attempt_count - 1)),
        MAX_WORKER_STARTUP_RETRY_DELAY_SECONDS,
    )


async def run_worker() -> None:
    attempt_count = 0

    async def run_with_bot() -> None:
        request = HTTPXRequest(
            connect_timeout=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
            read_timeout=TELEGRAM_READ_TIMEOUT_SECONDS,
            write_timeout=TELEGRAM_WRITE_TIMEOUT_SECONDS,
            pool_timeout=TELEGRAM_POOL_TIMEOUT_SECONDS,
        )
        async with Bot(BOT_TOKEN, request=request) as bot:
            await ReminderWorker(bot).run()

    async def retry_after_network_error(error: BaseException) -> None:
        nonlocal attempt_count
        attempt_count += 1
        delay_seconds = startup_retry_delay(attempt_count)
        logger.warning(
            "Reminder worker could not connect to Telegram; retrying in %s seconds: %s",
            delay_seconds,
            error,
        )
        await asyncio.sleep(delay_seconds)

    while True:
        await try_async(
            run_with_bot,
            handle_error=retry_after_network_error,
            exception_types=(NetworkError, TimedOut, OSError),
        )
