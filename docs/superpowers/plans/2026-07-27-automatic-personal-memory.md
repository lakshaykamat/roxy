# Automatic Personal Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically save durable user facts, choose tools in the main Roxy call, and log complete chat/tool activity without separate routing or memory-classifier calls.

**Architecture:** The main chat completion receives the available native tool definitions and decides whether to reply directly or call a tool. A new automatic-memory prompt rule makes the model call `save_brain_item` for durable user facts. That tool persists model-provided metadata directly with an idempotent capture key; relationship analysis remains the existing 3 AM batch responsibility. Full payload logging stays in the chat handler.

**Tech Stack:** Python, OpenAI Chat Completions tool calling, SQLite/FTS5, standard-library `unittest`.

## Global Constraints

- Persist only durable user facts: identity, preferences, work, people, projects, goals, routines, and long-lived context.
- Automatically persist sensitive user facts too, as requested.
- Do not save greetings, questions, transient updates, ordinary chat, assistant text, clock context, or web-research output.
- The main chat model is the tool classifier. Do not call `classify_tool_intent` for normal chat and do not add a separate memory-classifier call.
- Supply all registered tools with `tool_choice=None`; native tool calling decides whether an action is required.
- `save_brain_item` metadata supplied by the main model is stored directly. Do not make a second Brain-analysis or relation-analysis model call during chat.
- The existing 3 AM introspection job is solely responsible for batched relation analysis.
- Preserve the existing auto-capture pause switch and use `src/core/errors.py` for errors.
- Deduplicate retries with a deterministic Telegram message-based capture key.
- Log full local payloads; do not write secrets to source control or change log destinations.
- Do not commit; the user has not requested a commit.

---

### Task 1: Let the main model select every available tool

**Files:** Modify `src/handlers/chat.py`, `src/core/llm.py`, `src/prompts/system.py`; test `tests/test_chat.py`.

**Interfaces:** `async run_agent_loop(messages: list[object], *, capture_key: str | None = None) -> str`; `ask_llm(messages, *, tools=TOOL_DEFINITIONS, tool_choice=None)`.

- [ ] **Step 1: Write failing native-tool-selection tests.**

```python
async def test_normal_chat_uses_one_completion_with_all_tools_optional(self):
    response = response_with_text("Hey, I am good.")
    with patch("src.handlers.chat.ask_llm", new=AsyncMock(return_value=response)) as ask:
        self.assertEqual(await chat.run_agent_loop([user_message("How are you?")]), "Hey, I am good.")
    self.assertEqual(ask.await_count, 1)
    self.assertEqual(ask.await_args.kwargs["tools"], chat.TOOL_DEFINITIONS)
    self.assertIsNone(ask.await_args.kwargs["tool_choice"])

async def test_chat_does_not_call_the_separate_intent_router(self):
    with patch("src.handlers.chat.classify_tool_intent", new=AsyncMock()) as classify:
        await chat.run_agent_loop([user_message("Hello")])
    classify.assert_not_awaited()
```

- [ ] **Step 2: Run `python -m unittest tests.test_chat -v`; expect current intent-routing assertions to fail.**
- [ ] **Step 3: Remove `select_agent_tools` from `run_agent_loop`.** Pass `TOOL_DEFINITIONS` and `tool_choice=None` to its first `ask_llm` call, retain the same available tools for later tool-result rounds, and delete unused routing imports/functions from `src.handlers.chat.py`. Keep `classify_tool_intent` only if another caller still imports it; otherwise delete it and its tests from `src/core/llm.py`.
- [ ] **Step 4: Update `BASE_SYSTEM_PROMPT`.** Define the durable-memory rule: on a clear user identity, preference, work detail, person, project, goal, or routine, call `save_brain_item` with concise content, title, summary, valid item type, normalized tags, and `capture_mode="automatic"`. Instruct the model not to save questions, casual chat, temporary details, assistant messages, or system time. Retain explicit-save, reminders, web research, expense, archive, and deletion rules.
- [ ] **Step 5: Run `python -m unittest tests.test_chat tests.test_web_research -v`; expect PASS.**

### Task 2: Persist automatic memories without a second model analysis

**Files:** Modify `src/knowledge/tools.py`, `src/knowledge/brain_store.py` only if a small direct-save helper improves validation; test `tests/test_brain_store.py`, `tests/test_chat.py`.

**Interfaces:** `async save_brain_item(arguments: str, *, capture_key: str | None = None) -> dict[str, object]` continues to return `{"ok": bool, "brain_item": {"id": int, "title": str, "item_type": str}}`.

- [ ] **Step 1: Write failing automatic-save tests.**

```python
async def test_automatic_save_persists_the_main_model_metadata_without_analysis_call(self):
    arguments = json.dumps({
        "content": "Lakshay Kamat is an AI engineer.", "title": "Lakshay Kamat",
        "summary": "Lakshay works as an AI engineer.", "item_type": "fact",
        "tags": ["entity:lakshay kamat", "domain:career"], "capture_mode": "automatic",
    })
    with patch("src.knowledge.tools.analyze_and_save_item", new=AsyncMock()) as analyze:
        result = await tools.save_brain_item(arguments, capture_key="telegram:7:12:0")
    analyze.assert_not_awaited()
    self.assertTrue(result["ok"])

async def test_repeated_automatic_save_uses_one_capture_key(self):
    first = await tools.save_brain_item(arguments, capture_key="telegram:7:12:0")
    second = await tools.save_brain_item(arguments, capture_key="telegram:7:12:0")
    self.assertEqual(first["brain_item"]["id"], second["brain_item"]["id"])
```

- [ ] **Step 2: Run `python -m unittest tests.test_brain_store -v`; expect the first test to fail because automatic saves currently call `analyze_and_save_item`.**
- [ ] **Step 3: In `save_brain_item`, keep the existing validation and paused-capture check. For `capture_mode="automatic"`, call `brain_store.save_item` through `asyncio.to_thread` using the supplied title, summary, item type, normalized tags, source type `text`, and the passed capture key. Do not call `analyze_and_save_item` or relation functions.** Retain the analyzed path for non-automatic saves if it has callers.
- [ ] **Step 4: Ensure automatic saves with a missing capture key fail validation rather than create non-idempotent records.** Add a focused test for that case.
- [ ] **Step 5: Run `python -m unittest tests.test_brain_store tests.test_chat -v`; expect PASS.**

### Task 3: Preserve tool reply behavior and make activity logs complete

**Files:** Modify `src/handlers/chat.py`; test `tests/test_chat.py`.

- [ ] **Step 1: Write failing tool-loop and logging tests.**

```python
async def test_memory_tool_call_needs_one_follow_up_completion_for_a_reply(self):
    responses = [response_with_tool_call("save_brain_item", automatic_memory_arguments), response_with_text("Got it, Lakshay.")]
    with patch("src.handlers.chat.ask_llm", new=AsyncMock(side_effect=responses)) as ask:
        reply = await chat.run_agent_loop([user_message("I am an AI engineer")], capture_key="telegram:7:12")
    self.assertEqual((reply, ask.await_count), ("Got it, Lakshay.\n\nSaved to your brain: Lakshay Kamat.", 2))

with self.assertLogs("src.handlers.chat", "INFO") as logs:
    await chat.submit_chat_text(update, context, "I prefer dark mode")
self.assertIn("Received text message in chat 7: I prefer dark mode", logs.output)
self.assertIn('Tool call started: save_brain_item arguments={"content":"..."}', logs.output)
self.assertIn('Tool call completed: save_brain_item result={"ok": true}', logs.output)
self.assertIn("Chat response sent for chat 7: Done", logs.output)
```

- [ ] **Step 2: Run `python -m unittest tests.test_chat -v`; expect missing full-payload log messages and/or changed tool configuration assertions.**
- [ ] **Step 3: Keep the existing agent loop: execute the native tool call, append its JSON result, and make one follow-up completion for a natural confirmation.** Maintain the saved-item notice only after the save tool reports success. Tool-request messages take two model calls; normal chat stays at one.
- [ ] **Step 4: Change logs to include full received user text, full sent reply, raw tool argument strings, and `json.dumps(result, default=str)` tool results.** Retain the chat ID, tool name, intent only if available from a tool result, and success state.
- [ ] **Step 5: Run `python -m unittest tests.test_chat -v`; expect PASS.**

### Task 4: Keep relationship analysis at 3 AM and document call counts

**Files:** Modify `src/knowledge/introspection.py` and `tests/test_introspection.py` only if coverage is missing; modify `README.md`; all affected tests.

- [ ] **Step 1: Add a regression test proving an automatic chat save does not call `relation_candidates`, while `refresh_brain_connections` remains responsible for relation analysis.**
- [ ] **Step 2: Run `python -m unittest tests.test_introspection tests.test_brain_analysis -v`; expect PASS.**
- [ ] **Step 3: Document the call budget:** normal chat is one model completion; a tool or automatic-memory save is two completions; explicit capture/tool loops can require one completion per tool-result round. State that no separate intent-router, memory-classifier, per-message Brain-analysis, or per-message relation-analysis call occurs.
- [ ] **Step 4: Run `python -m unittest discover -s tests -v`; expect PASS.**
- [ ] **Step 5: Run `git diff --check -- src tests README.md .env.example docs` and `git status --short`; confirm `.env`, `roxy.db`, `scripts/start.sh`, and `todo.todo` are neither altered by this work nor staged.**

## Plan self-review

- Native tool choice replaces the separate intent and memory decision calls: Task 1.
- Automatic-memory persistence uses the main model metadata and avoids Brain analysis: Task 2.
- One-call normal chat, two-call tool/memory chat, full logs, and truthful save notices: Task 3.
- Batched 3 AM relationship analysis, documentation, and complete regression: Task 4.
- The plan excludes generic chat retention, hidden reasoning, automatic web-research storage, per-message relation analysis, external logging services, and commits.
