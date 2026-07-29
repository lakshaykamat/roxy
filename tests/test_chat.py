import asyncio
import os
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from telegram.error import TimedOut

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.handlers import chat
from src.core.debounce import DebounceCoordinator, PendingMessage
from src.reminders.repository import ScheduledTask


class TranscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_voice_downloads_file_and_uses_configured_model(self):
        from src.core.transcription import transcribe_voice

        telegram_file = MagicMock()
        telegram_file.download_to_drive = AsyncMock()
        response = SimpleNamespace(text="नमस्ते, कल 9 बजे याद दिलाना")

        with patch("src.core.transcription.client.audio.transcriptions.create", return_value=response) as create, patch(
            "src.core.transcription.TemporaryDirectory"
        ) as temporary_directory, patch("src.core.transcription.Path.open", create=True) as open_file, patch(
            "src.core.transcription.logger"
        ) as logger:
            temporary_directory.return_value.__enter__.return_value = "/tmp/transcription"
            open_file.return_value.__enter__.return_value = MagicMock()
            result = await transcribe_voice(telegram_file)

        expected_path = Path("/tmp/transcription/voice.ogg")
        telegram_file.download_to_drive.assert_awaited_once_with(expected_path)
        self.assertEqual(result, "नमस्ते, कल 9 बजे याद दिलाना")
        self.assertEqual(create.call_args.kwargs["model"], config.OPENAI_TRANSCRIPTION_MODEL)
        self.assertIs(create.call_args.kwargs["file"], open_file.return_value.__enter__.return_value)
        logger.info.assert_has_calls(
            [
                call("Downloading voice message for transcription"),
                call(
                    "Submitting voice message for transcription with model %s",
                    config.OPENAI_TRANSCRIPTION_MODEL,
                ),
                call("Voice message transcription completed"),
            ]
        )


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def test_system_prompt_requests_short_plain_language(self):
        self.assertIn("casual, friendly female", chat.SYSTEM_PROMPT)
        self.assertIn("warm, chill, and a little playful", chat.SYSTEM_PROMPT)
        self.assertIn("plain, everyday English", chat.SYSTEM_PROMPT)
        self.assertIn("Talk only in English or Hinglish", chat.SYSTEM_PROMPT)
        self.assertIn("reply in Hindi, Urdu, or any other language or script", chat.SYSTEM_PROMPT)
        self.assertIn("one to three short sentences", chat.SYSTEM_PROMPT)
        self.assertIn("Ask only one question at a time", chat.SYSTEM_PROMPT)
        self.assertIn("Treat a bare clock time as its next occurrence", chat.SYSTEM_PROMPT)

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
                {
                    "role": "system",
                    "content": "Current time in Asia/Kolkata: 2099-01-02T19:00:00+05:30. This is system context, not user content. Never save it to the user's brain.",
                },
                {"role": "user", "content": "First thought\nand second"},
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
                "role": "system",
                "content": "Current time in Asia/Kolkata: 2099-01-02T19:00:00+05:30. This is system context, not user content. Never save it to the user's brain.",
            },
        )
        self.assertEqual(messages[3], {"role": "user", "content": "First thought"})

    def test_build_burst_messages_keeps_clock_context_out_of_user_content(self):
        pending_messages = [PendingMessage(4, "Save this info", AsyncMock())]

        with patch("src.handlers.chat.history.get_before", return_value=[]), patch(
            "src.handlers.chat.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value.isoformat.return_value = "2099-01-02T19:00:00+05:30"

            messages = chat.build_burst_messages(pending_messages)

        self.assertEqual(messages[-1], {"role": "user", "content": "Save this info"})
        self.assertEqual(
            messages[-2],
            {
                "role": "system",
                "content": "Current time in Asia/Kolkata: 2099-01-02T19:00:00+05:30. This is system context, not user content. Never save it to the user's brain.",
            },
        )

    async def test_message_during_model_call_becomes_next_burst(self):
        send_reply = AsyncMock()
        model_started = asyncio.Event()
        release_model = asyncio.Event()

        async def run_agent_loop(messages, *, capture_key=None):
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

    async def test_process_burst_retries_transient_reply_delivery(self):
        send_reply = AsyncMock(side_effect=[TimedOut(), "sent"])

        with patch("src.handlers.chat.history.get_before", return_value=[]), patch(
            "src.handlers.chat.history.add"
        ) as add, patch(
            "src.handlers.chat.run_agent_loop", new=AsyncMock(return_value="One reply")
        ), patch("src.core.errors.asyncio.sleep", new=AsyncMock()) as sleep:
            await chat.process_burst(7, [PendingMessage(1, "Hello", send_reply)])

        self.assertEqual(send_reply.await_count, 2)
        sleep.assert_awaited_once_with(chat.TELEGRAM_SEND_RETRY_DELAY_SECONDS)
        add.assert_called_once_with("assistant", "One reply")

    async def test_process_burst_does_not_persist_undelivered_reply(self):
        send_reply = AsyncMock(side_effect=TimedOut())

        with patch("src.handlers.chat.history.get_before", return_value=[]), patch(
            "src.handlers.chat.history.add"
        ) as add, patch(
            "src.handlers.chat.run_agent_loop", new=AsyncMock(return_value="One reply")
        ), patch("src.core.errors.asyncio.sleep", new=AsyncMock()):
            await chat.process_burst(7, [PendingMessage(1, "Hello", send_reply)])

        self.assertEqual(send_reply.await_count, chat.TELEGRAM_SEND_ATTEMPTS)
        add.assert_not_called()

    async def test_agent_loop_does_not_block_the_event_loop_during_model_call(self):
        final_response = SimpleNamespace(content="Done", tool_calls=None)
        response = SimpleNamespace(choices=[SimpleNamespace(message=final_response)])

        def create_response(**kwargs):
            time.sleep(0.05)
            return response

        with patch("src.core.llm.client.chat.completions.create", side_effect=create_response):
            task = asyncio.create_task(chat.run_agent_loop([{"role": "system", "content": "test"}]))
            await asyncio.sleep(0.01)

            self.assertFalse(task.done())
            self.assertEqual(await task, "Done")

    async def test_agent_loop_uses_model_defaults_for_temperature(self):
        final_response = SimpleNamespace(content="Done", tool_calls=None)
        response = SimpleNamespace(choices=[SimpleNamespace(message=final_response)])

        with patch("src.core.llm.client.chat.completions.create", return_value=response) as create:
            await chat.run_agent_loop([{"role": "system", "content": "test"}])

        self.assertNotIn("temperature", create.call_args.kwargs)

    async def test_normal_chat_uses_one_completion_with_all_tools_optional(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hey, I am good.", tool_calls=None))]
        )

        with patch("src.handlers.chat.ask_llm", new=AsyncMock(return_value=response)) as ask_llm:
            reply = await chat.run_agent_loop([{"role": "user", "content": "How are you?"}])

        self.assertEqual(reply, "Hey, I am good.")
        self.assertEqual(ask_llm.await_count, 1)
        self.assertEqual(ask_llm.await_args.kwargs["tools"], chat.TOOL_DEFINITIONS)
        self.assertIsNone(ask_llm.await_args.kwargs["tool_choice"])

    def test_system_prompt_captures_durable_context_from_expense_messages(self):
        self.assertIn(
            "A message can require multiple independent tool actions",
            chat.SYSTEM_PROMPT,
        )
        self.assertIn("Assess Brain capture", chat.SYSTEM_PROMPT)
        self.assertIn("Save sensitive durable facts too", chat.SYSTEM_PROMPT)

    async def test_activity_logs_include_full_chat_and_tool_payloads(self):
        tool_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="save_brain_item",
                arguments='{"content":"I prefer dark mode"}',
            ),
        )
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Done", tool_calls=None))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Done", tool_calls=None))]),
        ]
        update = MagicMock()
        update.effective_chat.id = 7
        update.message.chat.send_action = AsyncMock()
        context = MagicMock()
        send_reply = AsyncMock()

        with self.assertLogs("src.handlers.chat", "INFO") as logs, patch(
            "src.handlers.chat.debounce_coordinator"
        ), patch("src.handlers.chat.history.add", return_value=12), patch(
            "src.handlers.chat.ask_llm", new=AsyncMock(side_effect=responses)
        ), patch(
            "src.handlers.chat.execute_tool_call",
            return_value={"ok": True, "brain_item": {"title": "Dark mode"}},
        ), patch("src.handlers.chat.history.get_before", return_value=[]):
            await chat.submit_chat_text(update, context, "I prefer dark mode")
            await chat.run_agent_loop([{"role": "user", "content": "I prefer dark mode"}])
            await chat.process_burst(7, [PendingMessage(12, "I prefer dark mode", send_reply)])

        output = "\n".join(logs.output)
        self.assertIn("Received text message in chat 7: I prefer dark mode", output)
        self.assertIn('Tool call started: save_brain_item arguments={"content":"I prefer dark mode"}', output)
        self.assertIn('Tool call completed: save_brain_item ok=True result={"ok": true', output)
        self.assertIn("Chat response sent for chat 7: Done", output)

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
        with patch("src.reminders.create_tool.repository.create_task", return_value=task):
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
        with patch("src.reminders.create_tool.repository.create_task") as create_task:
            result = chat.execute_tool_call(
                "schedule_task",
                '{"title":"Reminder","due_at":"2099-01-02T19:00:00+05:30"}',
            )

        self.assertFalse(result["ok"])
        self.assertIn("what to remind", result["error"])
        create_task.assert_not_called()

    def test_execute_tool_call_clears_active_reminders(self):
        with patch("src.reminders.manage_tool.repository.clear_active_tasks", return_value=15):
            result = chat.execute_tool_call("manage_reminders", '{"action":"clear"}')

        self.assertEqual(result, {"ok": True, "cleared_count": 15})

    def test_execute_tool_call_removes_selected_reminders(self):
        with patch("src.reminders.manage_tool.repository.complete_tasks", return_value=2):
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
        with patch("src.core.llm.client.chat.completions.create", side_effect=responses), patch(
            "src.handlers.chat.execute_tool_call", return_value={"ok": True}
        ):
            reply = await chat.run_agent_loop([{"role": "system", "content": "test"}])

        self.assertEqual(reply, "Done — I'll remind you.")

    async def test_agent_loop_appends_saved_brain_item_notice(self):
        tool_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="save_brain_item", arguments="{}"),
        )
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Nice idea.", tool_calls=None))]),
        ]
        with patch(
            "src.handlers.chat.ask_llm", new=AsyncMock(side_effect=responses)
        ), patch(
            "src.handlers.chat.execute_tool_call",
            return_value={"ok": True, "brain_item": {"title": "Freelancer app"}},
        ):
            reply = await chat.run_agent_loop([{"role": "user", "content": "Build an app"}], capture_key="telegram:7:1:1")

        self.assertEqual(reply, "Nice idea.\n\nSaved to your brain: Freelancer app.")

    async def test_agent_loop_uses_unique_capture_keys_for_multiple_automatic_saves(self):
        tool_calls = [
            SimpleNamespace(
                id=f"call_{index}",
                function=SimpleNamespace(name="save_brain_item", arguments="{}"),
            )
            for index in range(2)
        ]
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=tool_calls))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Saved.", tool_calls=None))]),
        ]
        with patch("src.handlers.chat.ask_llm", new=AsyncMock(side_effect=responses)), patch(
            "src.handlers.chat.execute_tool_call",
            return_value={"ok": True, "brain_item": {"title": "Fact"}},
        ) as execute:
            await chat.run_agent_loop(
                [{"role": "user", "content": "Two durable facts"}],
                capture_key="telegram:7:12:12",
            )

        self.assertEqual(
            [call.kwargs["capture_key"] for call in execute.call_args_list],
            ["telegram:7:12:12:0", "telegram:7:12:12:1"],
        )

    def test_saved_titles_from_capture_result_returns_each_title(self):
        titles = chat.saved_titles_from_tool_result(
            "capture_brain_content",
            {"brain_items": [{"title": "First link"}, {"title": "Second link"}, {"title": 3}]},
        )

        self.assertEqual(titles, ["First link", "Second link"])

    async def test_agent_loop_reports_saved_item_when_round_limit_is_reached(self):
        tool_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="save_brain_item", arguments="{}"),
        )
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))])
        with patch.object(config, "MAX_TOOL_CALL_ROUNDS", 1), patch(
            "src.handlers.chat.ask_llm", new=AsyncMock(return_value=response)
        ), patch(
            "src.handlers.chat.execute_tool_call",
            return_value={"ok": True, "brain_item": {"title": "Freelancer app"}},
        ):
            reply = await chat.run_agent_loop([{"role": "user", "content": "Build an app"}])

        self.assertEqual(
            reply,
            "Saved to your brain: Freelancer app. I couldn't finish the remaining request; please try that part again.",
        )

    async def test_agent_loop_keeps_selected_tools_after_a_tool_result(self):
        list_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="manage_reminders", arguments='{"action":"list"}'),
        )
        remove_call = SimpleNamespace(
            id="call_2",
            function=SimpleNamespace(
                name="manage_reminders", arguments='{"action":"remove","task_ids":[55]}'
            ),
        )
        responses = [
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[list_call]))]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[remove_call]))]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Removed it.", tool_calls=None))]
            ),
        ]

        with patch(
            "src.handlers.chat.ask_llm", new=AsyncMock(side_effect=responses)
        ) as ask_llm, patch(
            "src.handlers.chat.execute_tool_call", return_value={"ok": True}
        ):
            reply = await chat.run_agent_loop([{"role": "user", "content": "Delete lunch"}])

        self.assertEqual(reply, "Removed it.")
        self.assertEqual(ask_llm.await_count, 3)
        for call_args in ask_llm.await_args_list:
            self.assertEqual(call_args.kwargs["tools"], chat.TOOL_DEFINITIONS)
            self.assertIsNone(call_args.kwargs["tool_choice"])

    async def test_agent_loop_stops_after_tool_call_limit(self):
        tool_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="schedule_task", arguments="{}"),
        )
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))])
        with patch("src.core.llm.client.chat.completions.create", return_value=response), patch(
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
            "src.core.llm.client.chat.completions.create", return_value=response
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
