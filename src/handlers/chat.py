import asyncio
import inspect
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import NetworkError
from telegram.ext import ContextTypes
from src import config
from src.prompts.system import SYSTEM_PROMPT
from src.agent.tool_registry import (
    available_tool_intents,
    execute_tool_call,
    tool_definitions_for_intent,
)
from src.core.debounce import DebounceCoordinator, PendingMessage
from src.core.errors import log_async_error, retry_async, try_async
from src.conversations import history
from src.knowledge import brain
from src.core.llm import ask_llm, classify_tool_intent
from src.core.transcription import transcribe_voice

logger = logging.getLogger(__name__)
FALLBACK_REPLY = "Sorry, I hit a snag. Please send that again in a moment."
EMPTY_TRANSCRIPT_REPLY = "I couldn't understand that voice note. Please try again."
TELEGRAM_SEND_ATTEMPTS = 3
TELEGRAM_SEND_RETRY_DELAY_SECONDS = 1


def build_photo_message(
    history_before: list[dict], image_url: str, caption: str
) -> list[object]:
    image_part = {"type": "image_url", "image_url": {"url": image_url}}
    content: list[object] = []
    if caption:
        content.append({"type": "text", "text": caption})
    content.append(image_part)
    context = brain_context(caption)
    messages: list[object] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    if context:
        messages.append({"role": "system", "content": context})
    messages.extend(history_before)
    messages.append({"role": "user", "content": content})
    return messages


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
        image_url = file.file_path
        history_before = history.get_before(message_id)
        messages = build_photo_message(history_before, image_url, caption)
        reply = await run_agent_loop(
            messages, capture_key=f"telegram:{chat_id}:{message_id}:{message_id}"
        )
        history.add("assistant", reply)
        await context.bot.send_message(chat_id, reply)
        logger.info("Photo response sent for chat %s", chat_id)

    async def send_fallback(_: BaseException) -> None:
        logger.exception("Unable to process photo for chat %s", chat_id)
        await log_async_error(
            lambda: context.bot.send_message(chat_id, FALLBACK_REPLY),
            logger=logger,
            error_message="Unable to send fallback reply for photo in chat %s",
            error_args=(chat_id,),
        )

    await try_async(process_photo, handle_error=send_fallback)


async def submit_chat_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    chat_id = update.effective_chat.id
    logger.info("Received text message in chat %s", chat_id)
    message_id = history.add("user", text)

    debounce_coordinator.submit(
        chat_id,
        PendingMessage(message_id, text, context.bot.send_message),
    )
    await log_async_error(
        lambda: update.message.chat.send_action("typing"),
        logger=logger,
        error_message="Unable to send typing action for chat %s",
        error_args=(chat_id,),
    )


async def voice_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    voice = update.message.voice

    await log_async_error(
        lambda: update.message.chat.send_action("record_voice"),
        logger=logger,
        error_message="Unable to send record_voice action for chat %s",
        error_args=(chat_id,),
    )

    async def process_voice() -> None:
        telegram_file = await context.bot.get_file(voice.file_id)
        transcript = await transcribe_voice(telegram_file)
        if not transcript.strip():
            await context.bot.send_message(chat_id, EMPTY_TRANSCRIPT_REPLY)
            return
        await submit_chat_text(update, context, transcript)

    async def send_fallback(_: BaseException) -> None:
        logger.exception("Unable to process voice message for chat %s", chat_id)
        await log_async_error(
            lambda: context.bot.send_message(chat_id, FALLBACK_REPLY),
            logger=logger,
            error_message="Unable to send fallback reply for voice message in chat %s",
            error_args=(chat_id,),
        )

    await try_async(process_voice, handle_error=send_fallback)


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await submit_chat_text(update, context, update.message.text)


async def process_burst(chat_id: int, pending_messages: list[PendingMessage]) -> None:
    send_reply = pending_messages[-1].send_reply

    async def create_reply() -> str:
        return await run_agent_loop(
            build_burst_messages(pending_messages),
            capture_key=f"telegram:{chat_id}:{pending_messages[0].id}:{pending_messages[-1].id}",
        )

    async def send_with_retry(text: str) -> object:
        return await retry_async(
            lambda: send_reply(chat_id, text),
            attempts=TELEGRAM_SEND_ATTEMPTS,
            retry_delay_seconds=TELEGRAM_SEND_RETRY_DELAY_SECONDS,
            logger=logger,
            error_message=f"Unable to send Telegram reply for chat {chat_id}",
            exception_types=NetworkError,
        )

    async def send_fallback(_: BaseException) -> None:
        logger.exception("Unable to process chat burst for chat %s", chat_id)
        await log_async_error(
            lambda: send_with_retry(FALLBACK_REPLY),
            logger=logger,
            error_message="Unable to send fallback reply for chat %s",
            error_args=(chat_id,),
        )

    reply = await try_async(create_reply, handle_error=send_fallback)
    if reply is None:
        return

    delivered = await log_async_error(
        lambda: send_with_retry(reply),
        logger=logger,
        error_message="Unable to deliver chat response for chat %s",
        error_args=(chat_id,),
    )
    if delivered is None:
        return

    history.add("assistant", reply)
    logger.info("Chat response sent for chat %s", chat_id)


def build_burst_messages(pending_messages: list[PendingMessage]) -> list[object]:
    current_time = datetime.now(ZoneInfo(config.TASK_TIMEZONE)).isoformat()
    user_message = "\n".join(message.text for message in pending_messages)
    user_message += f"\n\nCurrent time in {config.TASK_TIMEZONE}: {current_time}"

    context = brain_context(user_message)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *([{"role": "system", "content": context}] if context else []),
        *history.get_before(pending_messages[0].id),
        {"role": "user", "content": user_message},
    ]


async def select_agent_tools(messages: list[object]) -> tuple[list[object] | None, str | None]:
    if (
        not messages
        or not isinstance(messages[-1], dict)
        or messages[-1].get("role") != "user"
    ):
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


async def run_agent_loop(messages: list[object], *, capture_key: str | None = None) -> str:
    tools, tool_choice = await select_agent_tools(messages)
    saved_titles: list[str] = []
    for _ in range(config.MAX_TOOL_CALL_ROUNDS):
        response = await ask_llm(messages, tools=tools, tool_choice=tool_choice)
        tool_choice = None
        message = response.choices[0].message
        if not message.tool_calls:
            reply = message.content or "Sorry, I couldn't prepare a response."
            return append_saved_item_notice(reply, saved_titles)

        messages.append(message)
        for tool_call in message.tool_calls:
            logger.info("Tool call started: %s", tool_call.function.name)
            result = execute_tool_call(
                tool_call.function.name,
                tool_call.function.arguments,
                capture_key=capture_key,
            )
            if inspect.isawaitable(result):
                result = await result
            succeeded = result.get("ok") if isinstance(result, dict) else False
            if succeeded and tool_call.function.name == "save_brain_item":
                item = result.get("brain_item")
                if isinstance(item, dict) and isinstance(item.get("title"), str):
                    saved_titles.append(item["title"])
            logger.info("Tool call completed: %s ok=%s", tool_call.function.name, succeeded)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    if saved_titles:
        return (
            f"Saved to your brain: {saved_titles[-1]}. "
            "I couldn't finish the remaining request; please try that part again."
        )
    return "I couldn't finish that just now. Please try again in a moment."


def append_saved_item_notice(reply: str, saved_titles: list[str]) -> str:
    if not saved_titles:
        return reply
    return f"{reply}\n\nSaved to your brain: {saved_titles[-1]}."


def brain_context(text: str) -> str:
    items = brain.search_items(text, 8)
    if not items:
        return ""
    lines = (f"- [{item.id}] {item.content}" for item in items)
    return "Relevant brain items:\n" + "\n".join(lines)
