from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from src.config import BOT_TOKEN, ALLOWED_USER_ID
from src.handlers.commands import start, reset
from src.handlers.chat import chat


def allowed_only(handler):
    async def wrapper(update: Update, context):
        if update.effective_user.id != ALLOWED_USER_ID:
            return
        return await handler(update, context)
    return wrapper


app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", allowed_only(start)))
app.add_handler(CommandHandler("reset", allowed_only(reset)))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, allowed_only(chat)))

print("Roxy is online 🤖")
app.run_polling()
