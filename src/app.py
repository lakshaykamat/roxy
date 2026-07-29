import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src import config
from src.config import ALLOWED_USER_ID, BOT_TOKEN
from src.handlers.chat import chat, photo_chat, voice_chat
from src.handlers.commands import (
    BRAIN_BUTTON_TEXT,
    CANCEL_DELETE_BUTTON_TEXT,
    CONFIRM_DELETE_BUTTON_TEXT,
    DELETE_DATA_BUTTON_TEXT,
    EXPORT_DATA_BUTTON_TEXT,
    HELP_BUTTON_TEXT,
    TASKS_BUTTON_TEXT,
    complete_task_callback,
    cancel_data_deletion,
    confirm_data_deletion,
    export_data,
    list_brain,
    list_tasks,
    request_data_deletion,
    show_help,
    start,
)
from src.core.errors import try_async
from src.knowledge.indexing import source_indexer
from src.web import app as web_app
from src.expenses import client as expense_tracker_client

logger = logging.getLogger(__name__)


def allowed_only(
    handler: Callable[[Update, Any], Awaitable[object]],
) -> Callable[[Update, Any], Awaitable[object]]:
    async def wrapper(update: Update, context: Any) -> object:
        if update.effective_user.id != ALLOWED_USER_ID:
            return None
        return await handler(update, context)

    return wrapper


def create_telegram_application() -> Application:
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(config.TELEGRAM_CONNECT_TIMEOUT_SECONDS)
        .read_timeout(config.TELEGRAM_READ_TIMEOUT_SECONDS)
        .write_timeout(config.TELEGRAM_WRITE_TIMEOUT_SECONDS)
        .pool_timeout(config.TELEGRAM_POOL_TIMEOUT_SECONDS)
        .build()
    )
    application.add_handler(CommandHandler("start", allowed_only(start)))
    keyboard_handlers = {
        TASKS_BUTTON_TEXT: list_tasks,
        BRAIN_BUTTON_TEXT: list_brain,
        EXPORT_DATA_BUTTON_TEXT: export_data,
        DELETE_DATA_BUTTON_TEXT: request_data_deletion,
        CONFIRM_DELETE_BUTTON_TEXT: confirm_data_deletion,
        CANCEL_DELETE_BUTTON_TEXT: cancel_data_deletion,
        HELP_BUTTON_TEXT: show_help,
    }
    for button_text, handler in keyboard_handlers.items():
        application.add_handler(
            MessageHandler(filters.Regex(f"^{re.escape(button_text)}$"), allowed_only(handler))
        )
    application.add_handler(
        CallbackQueryHandler(allowed_only(complete_task_callback), pattern=r"^done:")
    )
    application.add_handler(
        MessageHandler(filters.PHOTO, allowed_only(photo_chat))
    )
    application.add_handler(
        MessageHandler(filters.VOICE, allowed_only(voice_chat))
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, allowed_only(chat))
    )
    return application


async def _shutdown_telegram_application(
    telegram_app: Application,
    *,
    application_initialized: bool,
    application_started: bool,
    polling_started: bool,
) -> None:
    if polling_started:
        logger.info("Stopping Telegram polling")
        await telegram_app.updater.stop()
        logger.info("Telegram polling shutdown complete")
    if application_started:
        await telegram_app.stop()
    if application_initialized:
        await telegram_app.shutdown()


async def _start_telegram_polling(telegram_app: Application) -> None:
    application_initialized = False
    application_started = False
    polling_started = False

    async def start_polling() -> None:
        nonlocal application_initialized, application_started, polling_started
        await telegram_app.initialize()
        application_initialized = True
        await telegram_app.start()
        application_started = True
        logger.info("Starting Telegram polling")
        await telegram_app.updater.start_polling()
        polling_started = True
        logger.info("Telegram polling started")

    async def shutdown_after_failure(error: BaseException) -> None:
        await _shutdown_telegram_application(
            telegram_app,
            application_initialized=application_initialized,
            application_started=application_started,
            polling_started=polling_started,
        )
        raise error

    await try_async(
        start_polling,
        handle_error=shutdown_after_failure,
        exception_types=BaseException,
    )


async def _serve_http(server: uvicorn.Server) -> None:
    logger.info("Starting HTTP server")
    await server.serve()
    if not server.started:
        raise RuntimeError("The HTTP server stopped before it started.")
    logger.info("HTTP server shutdown complete")


async def _shutdown_application_resources() -> None:
    client = expense_tracker_client._client
    if client is not None:
        await client.aclose()


async def run() -> None:
    logger.info("Starting application")
    telegram_app: Application | None = None
    telegram_started = False

    async def run_application() -> None:
        nonlocal telegram_app, telegram_started
        telegram_app = create_telegram_application()
        server = uvicorn.Server(
            uvicorn.Config(web_app, host="0.0.0.0", port=config.HTTP_PORT, workers=1)
        )
        await source_indexer.start()
        await _start_telegram_polling(telegram_app)
        telegram_started = True
        await _serve_http(server)

    async def log_lifecycle_failure(error: BaseException) -> None:
        logger.exception("Application lifecycle failed")
        raise error

    async def shutdown_application() -> None:
        if telegram_app is not None and telegram_started:
            await _shutdown_telegram_application(
                telegram_app,
                application_initialized=True,
                application_started=True,
                polling_started=True,
            )
        await source_indexer.stop()
        await _shutdown_application_resources()
        logger.info("Application shutdown complete")

    await try_async(
        run_application,
        handle_error=log_lifecycle_failure,
        finally_handler=shutdown_application,
    )
