import asyncio
import base64
import inspect
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes
from src import config
from src.prompts.system import SYSTEM_PROMPT
from src.tools.registry import (
    available_tool_intents,
    execute_tool_call,
    tool_definitions_for_intent,
)
from src.utils.debounce import DebounceCoordinator, PendingMessage
from src.utils.errors import log_async_error, try_async
from src.utils import history
from src.utils.llm import ask_llm, classify_tool_intent

logger = logging.getLogger(__name__)
FALLBACK_REPLY = "Sorry, I hit a snag. Please send that again in a moment."


def build_photo_message(
    history_before: list[dict], b64_jpeg: str, caption: str
) -> list[object]:
    image_part = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_jpeg}"}}
    content: list[object] = []
    if caption:
        content.append({"type": "text", "text": caption})
    content.append(image_part)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history_before,
        {"role": "user", "content": content},
    ]


async def photo_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    caption = update.message.caption or ""
    photo = update.message.photo[-1]
    history_text = f"[Image{': ' + caption if caption else ''}]"
    message_id = history.add("user", history_text)

    await log_async_error(
        lambda: update.message.chat.send_action("upload_photo"),
        logger=logger,
        error_message="Unable to send upload_photo action for chat %s",
        error_args=(chat_id,),
    )

    async def process_photo() -> None:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        b64 = base64.b64encode(photo_bytes).decode()
        history_before = history.get_before(message_id)
        messages = build_photo_message(history_before, b64, caption)
        reply = await run_agent_loop(messages)
        history.add("assistant", reply)
        await context.bot.send_message(chat_id, reply)
        logger.info("Replied to photo in chat %s", chat_id)

    async def send_fallback(_: BaseException) -> None:
        logger.exception("Unable to process photo for chat %s", chat_id)
        await log_async_error(
            lambda: context.bot.send_message(chat_id, FALLBACK_REPLY),
            logger=logger,
            error_message="Unable to send fallback reply for photo in chat %s",
            error_args=(chat_id,),
        )

    await try_async(process_photo, handle_error=send_fallback)


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(
        "Message from chat %s: %s", update.effective_chat.id, update.message.text
    )
    message_id = history.add("user", update.message.text)

    debounce_coordinator.submit(
        update.effective_chat.id,
        PendingMessage(message_id, update.message.text, context.bot.send_message),
    )
    await log_async_error(
        lambda: update.message.chat.send_action("typing"),
        logger=logger,
        error_message="Unable to send typing action for chat %s",
        error_args=(update.effective_chat.id,),
    )


async def process_burst(chat_id: int, pending_messages: list[PendingMessage]) -> None:
    send_reply = pending_messages[-1].send_reply

    async def process_reply() -> None:
        reply = await run_agent_loop(build_burst_messages(pending_messages))
        history.add("assistant", reply)
        await send_reply(chat_id, reply)
        logger.info("Replied to chat %s: %s", chat_id, reply)

    async def send_fallback(_: BaseException) -> None:
        logger.exception("Unable to process chat burst for chat %s", chat_id)
        await log_async_error(
            lambda: send_reply(chat_id, FALLBACK_REPLY),
            logger=logger,
            error_message="Unable to send fallback reply for chat %s",
            error_args=(chat_id,),
        )

    await try_async(process_reply, handle_error=send_fallback)


def build_burst_messages(pending_messages: list[PendingMessage]) -> list[object]:
    current_time = datetime.now(ZoneInfo(config.TASK_TIMEZONE)).isoformat()
    user_message = "\n".join(message.text for message in pending_messages)
    user_message += f"\n\nCurrent time in {config.TASK_TIMEZONE}: {current_time}"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history.get_before(pending_messages[0].id),
        {"role": "user", "content": user_message},
    ]


async def select_agent_tools(messages: list[object]) -> tuple[list[object] | None, str | None]:
    if not messages or not isinstance(messages[-1], dict):
        return None, None

    latest_message = messages[-1]
    if latest_message.get("role") != "user":
        return None, None

    intent, requires_tool = await classify_tool_intent(messages, available_tool_intents())
    if intent is not None:
        logger.info("Tool intent decision: intent=%s requires_tool=%s", intent, requires_tool)
        if requires_tool:
            return tool_definitions_for_intent(intent), "required"
        return None, None

    logger.info("Tool intent decision: intent=general requires_tool=False")
    return None, None


debounce_coordinator = DebounceCoordinator(config.CHAT_DEBOUNCE_SECONDS, process_burst)


async def run_agent_loop(messages: list[object]) -> str:
    for _ in range(config.MAX_TOOL_CALL_ROUNDS):
        tools, tool_choice = await select_agent_tools(messages)
        response = await ask_llm(messages, tools=tools, tool_choice=tool_choice)
        message = response.choices[0].message
        if not message.tool_calls:
            return message.content or "Sorry, I couldn't prepare a response."

        messages.append(message)
        for tool_call in message.tool_calls:
            logger.info(
                "Tool call %s(%s)", tool_call.function.name, tool_call.function.arguments
            )
            result = execute_tool_call(tool_call.function.name, tool_call.function.arguments)
            if inspect.isawaitable(result):
                result = await result
            logger.info("Tool result %s: %s", tool_call.function.name, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    return "I couldn't finish that just now. Please try again in a moment."
