import asyncio

from openai import OpenAI
from openai.types.chat import ChatCompletion

from src.config import OPENAI_API_KEY, OPENAI_MODEL

client = OpenAI(api_key=OPENAI_API_KEY)


async def ask_llm(
    messages: list[object], *, tools: list[object] | None = None
) -> ChatCompletion:
    if tools is None:
        return await asyncio.to_thread(
            client.chat.completions.create,
            model=OPENAI_MODEL,
            messages=messages,
        )
    return await asyncio.to_thread(
        client.chat.completions.create,
        model=OPENAI_MODEL,
        messages=messages,
        tools=tools,
    )
