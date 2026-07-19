import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.utils.errors import try_async


@dataclass(frozen=True)
class PendingMessage:
    id: int
    text: str
    send_reply: Callable[[int, str], Awaitable[object]]


BurstHandler = Callable[[int, list[PendingMessage]], Awaitable[None]]


class DebounceCoordinator:
    def __init__(self, delay_seconds: float, handle_burst: BurstHandler):
        self.delay_seconds = delay_seconds
        self.handle_burst = handle_burst
        self.pending_messages: dict[int, list[PendingMessage]] = {}
        self.timer_tasks: dict[int, asyncio.Task[None]] = {}

    def submit(self, chat_id: int, message: PendingMessage) -> None:
        self.pending_messages.setdefault(chat_id, []).append(message)
        previous_task = self.timer_tasks.get(chat_id)
        if previous_task is not None:
            previous_task.cancel()
        self.timer_tasks[chat_id] = asyncio.create_task(self._deliver_after_delay(chat_id))

    async def _deliver_after_delay(self, chat_id: int) -> None:
        task = asyncio.current_task()

        async def deliver() -> None:
            await asyncio.sleep(self.delay_seconds)
            messages = self.pending_messages.pop(chat_id, [])
            if not messages:
                return
            if self.timer_tasks.get(chat_id) is task:
                self.timer_tasks.pop(chat_id, None)
            await self.handle_burst(chat_id, messages)

        async def re_raise(error: BaseException) -> None:
            raise error

        async def remove_timer() -> None:
            if self.timer_tasks.get(chat_id) is task:
                self.timer_tasks.pop(chat_id, None)

        await try_async(
            deliver,
            handle_error=re_raise,
            exception_types=BaseException,
            finally_handler=remove_timer,
        )
