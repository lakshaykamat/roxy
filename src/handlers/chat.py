import json
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI
from telegram import Update
from telegram.ext import ContextTypes
from src import config
from src.prompts.system import SYSTEM_PROMPT
from src.tools.registry import TOOL_DEFINITIONS, execute_tool_call
from src.utils import history

client = OpenAI(api_key=config.OPENAI_API_KEY)


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history.add("user", update.message.text)

    await update.message.chat.send_action("typing")

    current_time = datetime.now(ZoneInfo(config.TASK_TIMEZONE)).isoformat()
    system_message = f"{SYSTEM_PROMPT}\nCurrent time in {config.TASK_TIMEZONE}: {current_time}"
    messages = [{"role": "system", "content": system_message}] + history.get()
    reply = await run_agent_loop(messages)
    history.add("assistant", reply)
    await update.message.reply_text(reply)


async def run_agent_loop(messages: list[object]) -> str:
    for _ in range(config.MAX_TOOL_CALL_ROUNDS):
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            temperature=0.8,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            return message.content or "Sorry, I couldn't prepare a response."

        messages.append(message)
        for tool_call in message.tool_calls:
            result = execute_tool_call(tool_call.function.name, tool_call.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    return "I couldn't finish setting that reminder. Please try again with one clear schedule."
