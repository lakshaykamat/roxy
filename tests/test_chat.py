import asyncio
import os
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.handlers import chat
from src.utils.debounce import DebounceCoordinator, PendingMessage
from src.utils import llm
from src.utils.tasks import ScheduledTask


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def test_system_prompt_requests_short_plain_language(self):
        self.assertIn("casual, friendly female", chat.SYSTEM_PROMPT)
        self.assertIn("warm, chill, and a little playful", chat.SYSTEM_PROMPT)
        self.assertIn("plain, everyday English", chat.SYSTEM_PROMPT)
        self.assertIn("one to three short sentences", chat.SYSTEM_PROMPT)
        self.assertIn("Ask only one question at a time", chat.SYSTEM_PROMPT)

    async def test_chat_persists_each_message_and_submits_to_debounce(self):
        coordinator = MagicMock()
        update = MagicMock()
        update.message.text = "Hello"
        update.effective_chat.id = 7
        update.message.chat.send_action = AsyncMock()
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        with patch("src.handlers.chat.history.add", return_value=12) as add, patch(
            "src.handlers.chat.debounce_coordinator", coordinator
        ):
            await chat.chat(update, context)

        add.assert_called_once_with("user", "Hello")
        update.message.chat.send_action.assert_awaited_once_with("typing")
        pending_message = coordinator.submit.call_args.args[1]
        self.assertEqual(coordinator.submit.call_args.args[0], 7)
        self.assertEqual((pending_message.id, pending_message.text), (12, "Hello"))

    async def test_chat_schedules_message_when_typing_action_fails(self):
        coordinator = MagicMock()
        update = MagicMock()
        update.message.text = "Hello"
        update.effective_chat.id = 7
        update.message.chat.send_action = AsyncMock(side_effect=OSError("network down"))
        context = MagicMock()

        with patch("src.handlers.chat.history.add", return_value=12), patch(
            "src.handlers.chat.debounce_coordinator", coordinator
        ), self.assertLogs("src.handlers.chat", level="ERROR"):
            await chat.chat(update, context)

        coordinator.submit.assert_called_once()

    async def test_process_burst_combines_messages_and_uses_prior_history(self):
        send_reply = AsyncMock()
        pending_messages = [
            PendingMessage(4, "First thought", send_reply),
            PendingMessage(5, "and second", send_reply),
        ]
        reply = "One reply"

        with patch("src.handlers.chat.history.get_before", return_value=[{"role": "user", "content": "Earlier"}]) as get_before, patch(
            "src.handlers.chat.history.add"
        ) as add, patch("src.handlers.chat.run_agent_loop", new=AsyncMock(return_value=reply)) as run_agent_loop, patch(
            "src.handlers.chat.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value.isoformat.return_value = "2099-01-02T19:00:00+05:30"
            await chat.process_burst(7, pending_messages)

        get_before.assert_called_once_with(4)
        messages = run_agent_loop.await_args.args[0]
        self.assertEqual(
            messages[-2:],
            [
                {"role": "user", "content": "Earlier"},
                {
                    "role": "user",
                    "content": "First thought\nand second\n\nCurrent time in Asia/Kolkata: 2099-01-02T19:00:00+05:30",
                },
            ],
        )
        add.assert_called_once_with("assistant", reply)
        send_reply.assert_awaited_once_with(7, reply)

    def test_build_burst_messages_places_current_time_after_history(self):
        pending_messages = [PendingMessage(4, "First thought", AsyncMock())]
        previous_messages = [{"role": "assistant", "content": "Earlier reply"}]

        with patch("src.handlers.chat.history.get_before", return_value=previous_messages), patch(
            "src.handlers.chat.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value.isoformat.return_value = "2099-01-02T19:00:00+05:30"

            messages = chat.build_burst_messages(pending_messages)

        self.assertEqual(messages[0], {"role": "system", "content": chat.SYSTEM_PROMPT})
        self.assertEqual(messages[1], previous_messages[0])
        self.assertEqual(
            messages[2],
            {
                "role": "user",
                "content": "First thought\n\nCurrent time in Asia/Kolkata: 2099-01-02T19:00:00+05:30",
            },
        )

    async def test_message_during_model_call_becomes_next_burst(self):
        send_reply = AsyncMock()
        model_started = asyncio.Event()
        release_model = asyncio.Event()

        async def run_agent_loop(messages):
            model_started.set()
            await release_model.wait()
            return "reply"

        coordinator = DebounceCoordinator(0.01, chat.process_burst)
        with patch("src.handlers.chat.history.get_before", return_value=[]), patch(
            "src.handlers.chat.history.add"
        ), patch("src.handlers.chat.run_agent_loop", new=run_agent_loop):
            coordinator.submit(7, PendingMessage(1, "first", send_reply))
            await model_started.wait()
            coordinator.submit(7, PendingMessage(2, "second", send_reply))
            release_model.set()
            await asyncio.sleep(0.04)

        self.assertEqual(send_reply.await_count, 2)

    async def test_process_burst_sends_fallback_after_agent_failure(self):
        send_reply = AsyncMock()
        with patch("src.handlers.chat.history.get_before", return_value=[]), patch(
            "src.handlers.chat.run_agent_loop", new=AsyncMock(side_effect=RuntimeError("down"))
        ), self.assertLogs("src.handlers.chat", level="ERROR"):
            await chat.process_burst(7, [PendingMessage(1, "Hello", send_reply)])

        send_reply.assert_awaited_once_with(7, chat.FALLBACK_REPLY)

    async def test_agent_loop_does_not_block_the_event_loop_during_model_call(self):
        final_response = SimpleNamespace(content="Done", tool_calls=None)
        response = SimpleNamespace(choices=[SimpleNamespace(message=final_response)])

        def create_response(**kwargs):
            time.sleep(0.05)
            return response

        with patch("src.utils.llm.client.chat.completions.create", side_effect=create_response):
            task = asyncio.create_task(chat.run_agent_loop([{"role": "system", "content": "test"}]))
            await asyncio.sleep(0.01)

            self.assertFalse(task.done())
            self.assertEqual(await task, "Done")

    async def test_agent_loop_uses_model_defaults_for_temperature(self):
        final_response = SimpleNamespace(content="Done", tool_calls=None)
        response = SimpleNamespace(choices=[SimpleNamespace(message=final_response)])

        with patch("src.utils.llm.client.chat.completions.create", return_value=response) as create:
            await chat.run_agent_loop([{"role": "system", "content": "test"}])

        self.assertNotIn("temperature", create.call_args.kwargs)

    async def test_intent_router_uses_the_configured_low_cost_model(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"intent":"expenses","requires_tool":true}'
                    )
                )
            ]
        )

        with patch("src.utils.llm.client.chat.completions.create", return_value=response) as create:
            decision = await llm.classify_tool_intent(
                [{"role": "user", "content": "Add 10rs biscuit"}],
                {"expenses": "Track personal expenses"},
            )

        self.assertEqual(decision, ("expenses", True))
        self.assertEqual(create.call_args.kwargs["model"], config.INTENT_ROUTER_MODEL)
        self.assertEqual(create.call_args.kwargs["response_format"], {"type": "json_object"})

    async def test_expense_request_requires_an_expense_tool(self):
        messages = [{"role": "user", "content": "Add 10rs biscuit"}]

        with patch(
            "src.handlers.chat.available_tool_intents", return_value={"expenses": "Track expenses"}
        ), patch(
            "src.handlers.chat.classify_tool_intent",
            new=AsyncMock(return_value=("expenses", True)),
        ), patch(
            "src.handlers.chat.tool_definitions_for_intent", return_value=[{"name": "expense"}]
        ):
            tools, tool_choice = await chat.select_agent_tools(messages)

        self.assertEqual(tools, [{"name": "expense"}])
        self.assertEqual(tool_choice, "required")

    async def test_reminder_request_uses_only_reminder_tools(self):
        messages = [{"role": "user", "content": "Remind me to call Mum tomorrow at 10"}]

        with patch(
            "src.handlers.chat.available_tool_intents", return_value={"reminders": "Manage reminders"}
        ), patch(
            "src.handlers.chat.classify_tool_intent",
            new=AsyncMock(return_value=("reminders", True)),
        ), patch(
            "src.handlers.chat.tool_definitions_for_intent", return_value=[{"name": "reminder"}]
        ):
            tools, tool_choice = await chat.select_agent_tools(messages)

        self.assertEqual(tool_choice, "required")
        self.assertEqual(tools, [{"name": "reminder"}])

    async def test_tool_result_allows_a_normal_final_response(self):
        messages = [
            {"role": "user", "content": "Add 10rs biscuit"},
            {"role": "tool", "content": "{\"ok\": true}"},
        ]

        tools, tool_choice = await chat.select_agent_tools(messages)

        self.assertIsNone(tool_choice)
        self.assertEqual(tools, chat.TOOL_DEFINITIONS)

    def test_execute_tool_call_returns_task_details(self):
        task = ScheduledTask(
            4,
            "Call Dad",
            "Asia/Kolkata",
            "active",
            None,
            datetime(2099, 1, 2, 13, 30, tzinfo=timezone.utc),
            datetime(2099, 1, 1, tzinfo=timezone.utc),
            None,
        )
        with patch("src.tools.schedule_task.tasks.create_task", return_value=task):
            result = chat.execute_tool_call(
                "schedule_task",
                '{"title":"Call Dad","due_at":"2099-01-02T19:00:00+05:30"}',
            )

        self.assertEqual(result["task_id"], 4)
        self.assertEqual(result["recurrence"], "one-time")

    def test_execute_tool_call_rejects_wrong_argument_types(self):
        result = chat.execute_tool_call(
            "schedule_task",
            '{"title":[],"due_at":"2099-01-02T19:00:00+05:30"}',
        )

        self.assertFalse(result["ok"])
        self.assertIn("title", result["error"])

    def test_execute_tool_call_rejects_generic_reminder_title(self):
        with patch("src.tools.schedule_task.tasks.create_task") as create_task:
            result = chat.execute_tool_call(
                "schedule_task",
                '{"title":"Reminder","due_at":"2099-01-02T19:00:00+05:30"}',
            )

        self.assertFalse(result["ok"])
        self.assertIn("what to remind", result["error"])
        create_task.assert_not_called()

    def test_execute_tool_call_clears_active_reminders(self):
        with patch("src.tools.manage_tasks.tasks.clear_active_tasks", return_value=15):
            result = chat.execute_tool_call("manage_reminders", '{"action":"clear"}')

        self.assertEqual(result, {"ok": True, "cleared_count": 15})

    def test_execute_tool_call_removes_selected_reminders(self):
        with patch("src.tools.manage_tasks.tasks.complete_tasks", return_value=2):
            result = chat.execute_tool_call(
                "manage_reminders", '{"action":"remove","task_ids":[3,4]}'
            )

        self.assertEqual(result, {"ok": True, "removed_count": 2})

    def test_execute_tool_call_rejects_reminder_update_without_changes(self):
        result = chat.execute_tool_call("manage_reminders", '{"action":"update","task_id":3}')

        self.assertFalse(result["ok"])
        self.assertIn("Specify", result["error"])

    async def test_agent_loop_returns_final_response_after_tool_result(self):
        tool_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="schedule_task", arguments="{}"),
        )
        tool_response = SimpleNamespace(content=None, tool_calls=[tool_call])
        final_response = SimpleNamespace(content="Done — I'll remind you.", tool_calls=None)
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=tool_response)]),
            SimpleNamespace(choices=[SimpleNamespace(message=final_response)]),
        ]
        with patch("src.utils.llm.client.chat.completions.create", side_effect=responses), patch(
            "src.handlers.chat.execute_tool_call", return_value={"ok": True}
        ):
            reply = await chat.run_agent_loop([{"role": "system", "content": "test"}])

        self.assertEqual(reply, "Done — I'll remind you.")

    async def test_agent_loop_stops_after_tool_call_limit(self):
        tool_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="schedule_task", arguments="{}"),
        )
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))])
        with patch("src.utils.llm.client.chat.completions.create", return_value=response), patch(
            "src.handlers.chat.execute_tool_call", return_value={"ok": False}
        ):
            reply = await chat.run_agent_loop([{"role": "system", "content": "test"}])

        self.assertIn("couldn't finish", reply)

    async def test_agent_loop_uses_configured_tool_call_limit(self):
        tool_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="schedule_task", arguments="{}"),
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))]
        )
        with patch.object(config, "MAX_TOOL_CALL_ROUNDS", 1), patch(
            "src.utils.llm.client.chat.completions.create", return_value=response
        ), patch("src.handlers.chat.execute_tool_call", return_value={"ok": False}):
            reply = await chat.run_agent_loop([{"role": "system", "content": "test"}])

        self.assertIn("couldn't finish", reply)
