import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.handlers import chat
from src.utils.tasks import ScheduledTask


class ChatTests(unittest.IsolatedAsyncioTestCase):
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
        with patch.object(chat.client.chat.completions, "create", side_effect=responses), patch(
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
        with patch.object(chat.client.chat.completions, "create", return_value=response), patch(
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
        with patch.object(config, "MAX_TOOL_CALL_ROUNDS", 1), patch.object(
            chat.client.chat.completions, "create", return_value=response
        ), patch("src.handlers.chat.execute_tool_call", return_value={"ok": False}):
            reply = await chat.run_agent_loop([{"role": "system", "content": "test"}])

        self.assertIn("couldn't finish", reply)
