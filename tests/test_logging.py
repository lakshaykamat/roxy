import logging
import unittest

from src.utils.logging import configure_logging
from src.utils.errors import try_catch


class LoggingTests(unittest.TestCase):
    def test_configure_logging_sets_application_logger_to_info(self):
        root_logger = logging.getLogger()
        previous_handlers = root_logger.handlers[:]
        previous_level = root_logger.level
        def configure_and_assert() -> None:
            root_logger.handlers.clear()
            configure_logging()
            self.assertEqual(
                logging.getLogger("src.app").getEffectiveLevel(), logging.INFO
            )

        def restore_logging() -> None:
            root_logger.handlers[:] = previous_handlers
            root_logger.setLevel(previous_level)

        try_catch(configure_and_assert, finally_handler=restore_logging)
