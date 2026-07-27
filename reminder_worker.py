import asyncio

from src.core.logging import configure_logging
from src.reminders.worker import run_worker


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_worker())
