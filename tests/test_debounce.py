import asyncio
import unittest
from unittest.mock import AsyncMock

from src.utils.debounce import DebounceCoordinator, PendingMessage


class DebounceCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handle_burst = AsyncMock()
        self.send_reply = AsyncMock()
        self.coordinator = DebounceCoordinator(0.02, self.handle_burst)

    def message(self, message_id: int, text: str) -> PendingMessage:
        return PendingMessage(message_id, text, self.send_reply)

    async def test_combines_messages_in_arrival_order(self):
        self.coordinator.submit(1, self.message(1, "first"))
        self.coordinator.submit(1, self.message(2, "second"))

        await asyncio.sleep(0.04)

        self.handle_burst.assert_awaited_once()
        chat_id, messages = self.handle_burst.await_args.args
        self.assertEqual(chat_id, 1)
        self.assertEqual([message.text for message in messages], ["first", "second"])

    async def test_later_message_resets_timer(self):
        self.coordinator.submit(1, self.message(1, "first"))
        await asyncio.sleep(0.015)
        self.coordinator.submit(1, self.message(2, "second"))
        await asyncio.sleep(0.015)

        self.handle_burst.assert_not_awaited()
        await asyncio.sleep(0.02)
        self.handle_burst.assert_awaited_once()

    async def test_separate_chats_have_independent_timers(self):
        self.coordinator.submit(1, self.message(1, "one"))
        await asyncio.sleep(0.015)
        self.coordinator.submit(2, self.message(2, "two"))

        await asyncio.sleep(0.015)
        self.assertEqual(self.handle_burst.await_count, 1)
        await asyncio.sleep(0.02)
        self.assertEqual(self.handle_burst.await_count, 2)

    async def test_message_arriving_during_callback_becomes_next_burst(self):
        started = asyncio.Event()
        release = asyncio.Event()
        handled_bursts = []

        async def handle_burst(chat_id, messages):
            handled_bursts.append([message.text for message in messages])
            started.set()
            await release.wait()

        self.coordinator = DebounceCoordinator(0.01, handle_burst)
        self.coordinator.submit(1, self.message(1, "first"))
        await started.wait()
        self.coordinator.submit(1, self.message(2, "second"))
        release.set()

        await asyncio.sleep(0.03)

        self.assertEqual(handled_bursts, [["first"], ["second"]])
        self.assertEqual(self.coordinator.pending_messages, {})
        self.assertEqual(self.coordinator.timer_tasks, {})
