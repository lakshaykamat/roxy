from openai import OpenAI
from telegram import Update
from telegram.ext import ContextTypes
from src.config import OPENAI_API_KEY, OPENAI_MODEL
from src.prompts.system import SYSTEM_PROMPT
from src.utils import history

client = OpenAI(api_key=OPENAI_API_KEY)


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history.add("user", update.message.text)

    await update.message.chat.send_action("typing")

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history.get(),
        temperature=0.8,
    )

    reply = response.choices[0].message.content
    history.add("assistant", reply)
    await update.message.reply_text(reply)
