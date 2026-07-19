import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
ALLOWED_USER_ID: int = int(os.getenv("ALLOWED_USER_ID"))
OPENAI_MODEL: str = "gpt-4o-mini"
TASK_TIMEZONE: str = os.getenv("TASK_TIMEZONE", "Asia/Kolkata")
DATABASE_PATH: Path = Path(__file__).resolve().parents[1] / "roxy.db"
MAX_MESSAGES: int = 40
MAX_TOOL_CALL_ROUNDS: int = 3
CHAT_DEBOUNCE_SECONDS: float = 5
LEASE_DURATION: timedelta = timedelta(minutes=5)
MAX_DELIVERY_ATTEMPTS: int = 5
