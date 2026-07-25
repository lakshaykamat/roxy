import asyncio

from src.app import run
from src.config import require_valid_configuration
from src.utils.logging import configure_logging


def start() -> None:
    require_valid_configuration()
    configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    start()
