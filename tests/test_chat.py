import asyncio
import os
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
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


class TranscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_voice_downloads_file_and_uses_configured_model(self):
        from src.utils.transcription import transcribe_voice

        telegram_file = MagicMock()
        telegram_file.download_to_drive = AsyncMock()
        response = SimpleNamespace(text="नमस्ते, कल 9 बजे याद दिलाना")

        with patch("src.utils.transcription.client.audio.transcriptions.create", return_value=response) as create, patch(
            "src.utils.transcription.TemporaryDirectory"
        ) as temporary_directory, patch("src.utils.transcription.Path.open", create=True) as open_file:
            temporary_directory.return_value.__enter__.return_value = "/tmp/transcription"
            open_file.return_value.__enter__.return_value = MagicMock()
            result = await transcribe_voice(telegram_file)

        expected_path = Path("/tmp/transcription/voice.oga")
        telegram_file.download_to_drive.assert_awaited_once_with(expected_path)
        self.assertEqual(result, "नमस्ते, कल 9 बजे याद दिलाना")
        self.assertEqual(create.call_args.kwargs["model"], config.OPENAI_TRANSCRIPTION_MODEL)
        self.assertIs(create.call_args.kwargs["file"], open_file.return_value.__enter__.return_value)


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

    async def test_intent_router_uses_the_configured_model_for_expense_request(self):
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
                [{"role": "user", "content": "Add 21rs expense as hema aunty"}],
                {"expenses": "Track personal expenses"},
            )

        self.assertEqual(decision, ("expenses", True))
        self.assertEqual(create.call_args.kwargs["model"], config.INTENT_ROUTER_MODEL)
        router_prompt = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Use general only for clearly conversational messages", router_prompt)
        self.assertIn("Add 21rs expense as hema aunty", router_prompt)
        self.assertEqual(
            create.call_args.kwargs["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "tool_intent",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "intent": {"type": "string", "enum": ["general", "expenses"]},
                            "requires_tool": {"type": "boolean"},
                        },
                        "required": ["intent", "requires_tool"],
                        "additionalProperties": False,
                    },
                },
            },
        )

    async def test_intent_router_returns_no_tool_for_general_chat(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"intent":"general","requires_tool":false}'))]
        )

        with patch("src.utils.llm.client.chat.completions.create", return_value=response):
            decision = await llm.classify_tool_intent(
                [{"role": "user", "content": "How are you?"}],
                {"expenses": "Track personal expenses"},
            )

        self.assertEqual(decision, (None, False))

    async def test_expense_request_requires_an_expense_tool(self):
        messages = [{"role": "user", "content": "Add 21rs expense as hema aunty"}]

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

    async def test_non_executable_tool_request_does_not_expose_tools(self):
        messages = [{"role": "user", "content": "What expense categories can I use?"}]

        with patch(
            "src.handlers.chat.available_tool_intents", return_value={"expenses": "Track expenses"}
        ), patch(
            "src.handlers.chat.classify_tool_intent",
            new=AsyncMock(return_value=("expenses", False)),
        ):
            tools, tool_choice = await chat.select_agent_tools(messages)

        self.assertIsNone(tools)
        self.assertIsNone(tool_choice)

    async def test_tool_result_allows_a_normal_final_response(self):
        messages = [
            {"role": "user", "content": "Add 10rs biscuit"},
            {"role": "tool", "content": "{\"ok\": true}"},
        ]

        tools, tool_choice = await chat.select_agent_tools(messages)

        self.assertIsNone(tool_choice)
        self.assertIsNone(tools)

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


class PhotoChatTests(unittest.IsolatedAsyncioTestCase):
    def test_build_photo_message_without_caption(self):
        history_before = [{"role": "assistant", "content": "Hi there"}]
        url = "https://api.telegram.org/file/botTOKEN/photo.jpg"

        messages = chat.build_photo_message(history_before, url, "")

        self.assertEqual(messages[0], {"role": "system", "content": chat.SYSTEM_PROMPT})
        self.assertEqual(messages[1], {"role": "assistant", "content": "Hi there"})
        user_msg = messages[2]
        self.assertEqual(user_msg["role"], "user")
        content = user_msg["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "image_url")
        self.assertEqual(content[0]["image_url"]["url"], url)

    def test_build_photo_message_with_caption(self):
        url = "https://api.telegram.org/file/botTOKEN/photo.jpg"

        messages = chat.build_photo_message([], url, "What is this?")

        user_msg = messages[-1]
        content = user_msg["content"]
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0], {"type": "text", "text": "What is this?"})
        self.assertEqual(content[1]["type"], "image_url")

    async def test_photo_chat_downloads_image_stores_history_and_replies(self):
        update = MagicMock()
        update.effective_chat.id = 42
        update.message.photo = [MagicMock(file_id="file_abc")]
        update.message.caption = "Describe this"
        update.message.chat.send_action = AsyncMock()
        context = MagicMock()
        mock_file = AsyncMock()
        mock_file.file_path = "photos/file_abc.jpg"
        context.bot.get_file = AsyncMock(return_value=mock_file)
        context.bot.send_message = AsyncMock()

        with patch("src.handlers.chat.history.add", return_value=99) as add, \
             patch("src.handlers.chat.history.get_before", return_value=[]), \
             patch("src.handlers.chat.run_agent_loop", new=AsyncMock(return_value="It's a cat")) as loop, \
             patch("src.handlers.chat.history.add") as add2:
            add2.side_effect = [99, None]
            await chat.photo_chat(update, context)

        context.bot.get_file.assert_awaited_once_with("file_abc")
        sent_messages = loop.await_args.args[0]
        user_msg = sent_messages[-1]
        self.assertEqual(user_msg["role"], "user")
        content = user_msg["content"]
        image_part = next(p for p in content if p["type"] == "image_url")
        self.assertIn("photos/file_abc.jpg", image_part["image_url"]["url"])

    async def test_photo_chat_sends_fallback_on_error(self):
        update = MagicMock()
        update.effective_chat.id = 42
        update.message.photo = [MagicMock(file_id="file_abc")]
        update.message.caption = None
        update.message.chat.send_action = AsyncMock()
        context = MagicMock()
        context.bot.get_file = AsyncMock(side_effect=RuntimeError("download failed"))
        context.bot.send_message = AsyncMock()

        with patch("src.handlers.chat.history.add", return_value=99), \
             self.assertLogs("src.handlers.chat", level="ERROR"):
            await chat.photo_chat(update, context)

        context.bot.send_message.assert_awaited_once_with(42, chat.FALLBACK_REPLY)


class VoiceChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_chat_transcribes_then_submits_transcript_as_chat_text(self):
        update = MagicMock()
        update.effective_chat.id = 7
        update.message.voice.file_id = "voice_abc"
        update.message.chat.send_action = AsyncMock()
        context = MagicMock()
        context.bot.get_file = AsyncMock(return_value=MagicMock())

        with patch(
            "src.handlers.chat.transcribe_voice",
            new=AsyncMock(return_value="Kal 9 baje remind karna"),
        ) as transcribe, patch(
            "src.handlers.chat.submit_chat_text", new=AsyncMock()
        ) as submit:
            await chat.voice_chat(update, context)

        context.bot.get_file.assert_awaited_once_with("voice_abc")
        transcribe.assert_awaited_once_with(context.bot.get_file.return_value)
        submit.assert_awaited_once_with(update, context, "Kal 9 baje remind karna")
        update.message.chat.send_action.assert_awaited_once_with("record_voice")

    async def test_voice_chat_sends_fallback_when_transcription_fails(self):
        update = MagicMock()
        update.effective_chat.id = 7
        update.message.voice.file_id = "voice_abc"
        update.message.chat.send_action = AsyncMock()
        context = MagicMock()
        context.bot.get_file = AsyncMock(return_value=MagicMock())
        context.bot.send_message = AsyncMock()

        with patch(
            "src.handlers.chat.transcribe_voice",
            new=AsyncMock(side_effect=RuntimeError("bad audio")),
        ), patch("src.handlers.chat.submit_chat_text", new=AsyncMock()) as submit, self.assertLogs(
            "src.handlers.chat", level="ERROR"
        ):
            await chat.voice_chat(update, context)

        submit.assert_not_awaited()
        context.bot.send_message.assert_awaited_once_with(7, chat.FALLBACK_REPLY)

    async def test_voice_chat_asks_for_a_new_voice_note_when_transcript_is_empty(self):
        update = MagicMock()
        update.effective_chat.id = 7
        update.message.voice.file_id = "voice_abc"
        update.message.chat.send_action = AsyncMock()
        context = MagicMock()
        context.bot.get_file = AsyncMock(return_value=MagicMock())
        context.bot.send_message = AsyncMock()

        with patch(
            "src.handlers.chat.transcribe_voice", new=AsyncMock(return_value="  \n")
        ), patch("src.handlers.chat.submit_chat_text", new=AsyncMock()) as submit:
            await chat.voice_chat(update, context)

        submit.assert_not_awaited()
        context.bot.send_message.assert_awaited_once_with(7, chat.EMPTY_TRANSCRIPT_REPLY)
