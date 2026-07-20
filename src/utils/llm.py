import asyncio
import json
import logging

from openai import OpenAI
from openai.types.chat import ChatCompletion

from src.config import INTENT_ROUTER_MODEL, OPENAI_API_KEY, OPENAI_MODEL
from src.utils.errors import try_async

client = OpenAI(api_key=OPENAI_API_KEY)
logger = logging.getLogger(__name__)


def _tool_intent_schema(intents: dict[str, str]) -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "tool_intent",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": ["general", *intents]},
                    "requires_tool": {"type": "boolean"},
                },
                "required": ["intent", "requires_tool"],
                "additionalProperties": False,
            },
        },
    }


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
    examples: list[str] = []
    if "expenses" in intents:
        examples.extend(
            [
                '- "Add 21rs expense as hema aunty" -> expenses, requires_tool=true',
                '- "Paid ₹21 to Hema aunty" -> expenses, requires_tool=true',
            ]
        )
    if "reminders" in intents:
        examples.append(
            '- "Remind me to call Mum tomorrow at 10" -> reminders, requires_tool=true'
        )
    examples.append('- "How are you?" -> general, requires_tool=false')
    routing_examples = "\n".join(examples)

    async def classify() -> tuple[str | None, bool]:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=INTENT_ROUTER_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the conversation's latest user turn. Choose a listed intent whenever "
                        "the turn plausibly asks to use that capability, including informal or abbreviated "
                        "wording. Use general only for clearly conversational messages that imply no listed "
                        "capability. If uncertain between general and a listed intent, choose the listed intent. "
                        "Set requires_tool to true when the turn has enough information to execute an action "
                        "or retrieve facts now. "
                        "A confirmation or answer to a prior expense or reminder clarification can require "
                        "a tool. A user reporting an item and amount is an expense action and requires a tool.\n"
                        f"Available intents:\n{intent_options}\n"
                        f"Examples:\n{routing_examples}"
                    ),
                },
                *conversation,
            ],
            response_format=_tool_intent_schema(intents),
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        intent = result.get("intent")
        requires_tool = result.get("requires_tool")
        if intent == "general" and isinstance(requires_tool, bool):
            return None, False
        if intent not in intents or not isinstance(requires_tool, bool):
            return None, False
        return intent, requires_tool

    async def use_unrestricted_tools(_: BaseException) -> tuple[str | None, bool]:
        logger.exception("Unable to classify tool intent")
        return None, False

    return await try_async(classify, handle_error=use_unrestricted_tools)
