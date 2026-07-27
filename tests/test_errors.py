import logging
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src.core.errors import (
    log_async_error,
    retry_async,
    try_async,
    try_catch,
    try_catch_context,
)


class ErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_async_retries_transient_failure(self):
        operation = AsyncMock(side_effect=[OSError("network down"), "done"])

        with patch("src.core.errors.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await retry_async(
                operation,
                attempts=3,
                retry_delay_seconds=1,
                logger=logging.getLogger(__name__),
                error_message="Retrying operation after transient failure",
                exception_types=OSError,
            )

        self.assertEqual(result, "done")
        self.assertEqual(operation.await_count, 2)
        sleep.assert_awaited_once_with(1)

    async def test_try_async_returns_operation_result(self):
        async def operation() -> str:
            return "done"

        result = await try_async(operation)

        self.assertEqual(result, "done")

    async def test_try_async_logs_failure_and_returns_none(self):
        async def operation() -> None:
            raise OSError("network down")

        with self.assertLogs(__name__, level="ERROR"):
            result = await log_async_error(
                operation,
                logger=logging.getLogger(__name__),
                error_message="Operation failed",
            )

        self.assertIsNone(result)

    def test_try_catch_returns_error_handler_result(self):
        def operation() -> str:
            raise ValueError("invalid")

        result = try_catch(operation, handle_error=lambda error: str(error))

        self.assertEqual(result, "invalid")

    def test_try_catch_context_handles_errors_and_runs_finally_handler(self):
        events: list[str] = []

        with try_catch_context(
            handle_error=lambda error: events.append(str(error)),
            finally_handler=lambda: events.append("closed"),
        ):
            raise OSError("network down")

        self.assertEqual(events, ["network down", "closed"])
