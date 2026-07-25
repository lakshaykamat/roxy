import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _positive_integer(name: str, default: int = 0) -> int:
    value = os.getenv(name, "")
    return int(value) if value.isdigit() and int(value) > 0 else default


def _nonnegative_integer(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    return int(value) if value.isdigit() else default


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ALLOWED_USER_ID = _positive_integer("ALLOWED_USER_ID")
OPENAI_MODEL = "gpt-5-mini"
OPENAI_TRANSCRIPTION_MODEL = os.getenv(
    "OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"
)
INTENT_ROUTER_MODEL = os.getenv("INTENT_ROUTER_MODEL", "gpt-5-mini")
TASK_TIMEZONE = os.getenv("TASK_TIMEZONE", "Asia/Kolkata")
EXPENSE_TRACKER_API_KEY = os.getenv("EXPENSE_TRACKER_API_KEY")
EXPENSE_TRACKER_BASE_URL = os.getenv(
    "EXPENSE_TRACKER_BASE_URL", "https://busty-expense-tracker-api.vercel.app"
)
EXPENSE_TRACKER_TIMEOUT = float(os.getenv("EXPENSE_TRACKER_TIMEOUT", "10"))
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "INR")
EXPENSE_TRACKER_ENABLED = bool(EXPENSE_TRACKER_API_KEY)
DATABASE_PATH = Path(
    os.getenv("DATABASE_PATH", Path(__file__).resolve().parents[1] / "roxy.db")
)
HISTORY_RETENTION_DAYS = _nonnegative_integer("HISTORY_RETENTION_DAYS", 90)
MEMORY_RETENTION_DAYS = _nonnegative_integer("MEMORY_RETENTION_DAYS", 0)
MAX_MESSAGES = 40
MAX_TOOL_CALL_ROUNDS = 3
CHAT_DEBOUNCE_SECONDS = 5
LEASE_DURATION = timedelta(minutes=5)
MAX_DELIVERY_ATTEMPTS = 5


def validate_configuration() -> list[str]:
    errors: list[str] = []
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        errors.append("TELEGRAM_BOT_TOKEN is required.")
    if not os.getenv("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY is required.")
    if _positive_integer("ALLOWED_USER_ID") == 0:
        errors.append("ALLOWED_USER_ID must be a positive integer.")
    return errors


def require_valid_configuration() -> None:
    if errors := validate_configuration():
        raise RuntimeError("Invalid configuration: " + " ".join(errors))
