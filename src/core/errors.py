import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar


T = TypeVar("T")
ErrorHandler = Callable[[BaseException], T]
AsyncErrorHandler = Callable[[BaseException], Awaitable[T]]
ExceptionTypes = type[BaseException] | tuple[type[BaseException], ...]


def try_catch(
    operation: Callable[[], T],
    *,
    handle_error: ErrorHandler[T] | None = None,
    exception_types: ExceptionTypes = Exception,
    finally_handler: Callable[[], None] | None = None,
) -> T:
    try:
        return operation()
    except exception_types as error:
        if handle_error is None:
            raise
        return handle_error(error)
    finally:
        if finally_handler is not None:
            finally_handler()


async def try_async(
    operation: Callable[[], Awaitable[T]],
    *,
    handle_error: AsyncErrorHandler[T] | None = None,
    exception_types: ExceptionTypes = Exception,
    finally_handler: Callable[[], Awaitable[None]] | None = None,
) -> T:
    try:
        return await operation()
    except exception_types as error:
        if handle_error is None:
            raise
        return await handle_error(error)
    finally:
        if finally_handler is not None:
            await finally_handler()


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    retry_delay_seconds: float,
    logger: logging.Logger,
    error_message: str,
    exception_types: ExceptionTypes = Exception,
    should_retry: Callable[[BaseException], bool] | None = None,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except exception_types as error:
            if attempt == attempts or (should_retry is not None and not should_retry(error)):
                raise
            delay_seconds = retry_delay_seconds * (2 ** (attempt - 1))
            logger.warning(
                "%s; retrying in %s seconds (attempt %s of %s): %s",
                error_message,
                delay_seconds,
                attempt,
                attempts,
                error,
            )
            await asyncio.sleep(delay_seconds)

    raise RuntimeError("retry operation completed without a result")


@contextmanager
def try_catch_context(
    *,
    handle_error: Callable[[BaseException], None] | None = None,
    exception_types: ExceptionTypes = Exception,
    success_handler: Callable[[], None] | None = None,
    finally_handler: Callable[[], None] | None = None,
) -> Iterator[None]:
    try:
        yield
        if success_handler is not None:
            success_handler()
    except exception_types as error:
        if handle_error is None:
            raise
        handle_error(error)
    finally:
        if finally_handler is not None:
            finally_handler()


async def log_async_error(
    operation: Callable[[], Awaitable[T]],
    *,
    logger: logging.Logger,
    error_message: str,
    error_args: tuple[object, ...] = (),
) -> T | None:
    async def handle_error(_: BaseException) -> None:
        logger.exception(error_message, *error_args)

    return await try_async(operation, handle_error=handle_error)
