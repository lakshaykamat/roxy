import asyncio

from src.app import run
from src.config import require_valid_configuration
from src.core.logging import configure_logging
from src.knowledge.brain_store import initialize_schema


def start() -> None:
    require_valid_configuration()
    configure_logging()
    initialize_schema()
    asyncio.run(run())


if __name__ == "__main__":
    start()
