import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
ALLOWED_USER_ID: int = int(os.getenv("ALLOWED_USER_ID"))
OPENAI_MODEL: str = "gpt-5-mini"
TASK_TIMEZONE: str = os.getenv("TASK_TIMEZONE", "Asia/Kolkata")
EXPENSE_TRACKER_API_KEY: str | None = os.getenv("EXPENSE_TRACKER_API_KEY")
EXPENSE_TRACKER_BASE_URL: str = os.getenv(
    "EXPENSE_TRACKER_BASE_URL", "https://busty-expense-tracker-api.vercel.app"
)
EXPENSE_TRACKER_TIMEOUT: float = float(os.getenv("EXPENSE_TRACKER_TIMEOUT", "10"))
DEFAULT_CURRENCY: str = os.getenv("DEFAULT_CURRENCY", "INR")
# Expense tracking is optional: it is only offered when an API key is present.
EXPENSE_TRACKER_ENABLED: bool = bool(EXPENSE_TRACKER_API_KEY)
DATABASE_PATH: Path = Path(
    os.getenv("DATABASE_PATH", Path(__file__).resolve().parents[1] / "roxy.db")
)
MAX_MESSAGES: int = 40
MAX_TOOL_CALL_ROUNDS: int = 3
CHAT_DEBOUNCE_SECONDS: float = 5
LEASE_DURATION: timedelta = timedelta(minutes=5)
MAX_DELIVERY_ATTEMPTS: int = 5
