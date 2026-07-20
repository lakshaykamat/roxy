import asyncio
import json
import logging

from openai import OpenAI
from openai.types.chat import ChatCompletion

from src.config import INTENT_ROUTER_MODEL, OPENAI_API_KEY, OPENAI_MODEL
from src.utils.errors import try_async

client = OpenAI(api_key=OPENAI_API_KEY)
logger = logging.getLogger(__name__)


async def ask_llm(
    messages: list[object],
    *,
    tools: list[object] | None = None,
    tool_choice: str | None = None,
) -> ChatCompletion:
    if tools is None:
        return await asyncio.to_thread(
            client.chat.completions.create,
            model=OPENAI_MODEL,
            messages=messages,
        )
    options: dict[str, object] = {"tools": tools}
    if tool_choice is not None:
        options["tool_choice"] = tool_choice

    return await asyncio.to_thread(
        client.chat.completions.create,
        model=OPENAI_MODEL,
        messages=messages,
        **options,
    )


async def classify_tool_intent(
    messages: list[object], intents: dict[str, str]
) -> tuple[str | None, bool]:
    if not intents:
        return None, False

    conversation = [
        {"role": message["role"], "content": message["content"]}
        for message in messages
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]
    if not conversation:
        return None, False

    intent_options = "\n".join(
        f"- {name}: {description}" for name, description in intents.items()
    )

    async def classify() -> tuple[str | None, bool]:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=INTENT_ROUTER_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the conversation's latest user turn. Choose an intent only when "
                        "the user is asking Roxy to use that capability. Set requires_tool to true "
                        "only when the turn has enough information to execute an action or retrieve "
                        "facts now. A confirmation of a previously identified action can require a tool. "
                        "Return only JSON with intent (one listed intent or null) and requires_tool (boolean).\n"
                        f"Available intents:\n{intent_options}"
                    ),
                },
                *conversation,
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        intent = result.get("intent")
        requires_tool = result.get("requires_tool")
        if intent not in intents or not isinstance(requires_tool, bool):
            return None, False
        return intent, requires_tool

    async def use_unrestricted_tools(_: BaseException) -> tuple[str | None, bool]:
        logger.exception("Unable to classify tool intent")
        return None, False

    return await try_async(classify, handle_error=use_unrestricted_tools)
