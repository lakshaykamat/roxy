import asyncio

from src.utils.logging import configure_logging
from src.worker import run_worker


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_worker())
