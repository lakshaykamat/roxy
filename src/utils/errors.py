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
