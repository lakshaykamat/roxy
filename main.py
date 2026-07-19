import asyncio

from src.app import run
from src.utils.logging import configure_logging


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run())
