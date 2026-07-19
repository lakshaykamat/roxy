from telegram import Update
from telegram.ext import ContextTypes
from src.utils import history


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history.clear()
    await update.message.reply_text("Hey! I'm Roxy 👋 What's on your mind? (Type /reset to start fresh.)")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history.clear()
    await update.message.reply_text("Memory wiped. Fresh start! 🧹")
