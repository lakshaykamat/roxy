# Unified SQLite Second Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `brain_items` Roxy's single local model for meaningful personal information, including stable facts, ideas, projects, and scheduled tasks, with SQLite search and a tag-based thought map.

**Architecture:** Chat history remains operational data. All user knowledge moves into `brain_items`; a task is simply a brain item with scheduling fields. `reminder_deliveries` remains separate because one scheduled brain item can have many notification attempts over time. `src/utils/brain.py` owns this schema and exposes focused APIs to chat, tools, commands, the reminder worker, export/delete, and the authenticated web map.

**Tech Stack:** Python 3.14, SQLite/FTS5, the existing OpenAI intent router and tool-calling loop, python-telegram-bot, FastAPI, server-rendered HTML/SVG, standard-library `unittest`.

## Global Constraints

- Use the existing `roxy.db`; do not add a vector database or third-party dependency.
- `brain_items` is the source of truth for all meaningful personal data. Remove the `memories` and `tasks` tables during migration.
- Keep `messages` separate: retained conversation transcripts are not automatically second-brain knowledge.
- Keep reminder deliveries separate: notification retries and history are not properties of a thought or task.
- Remove operational heartbeat persistence and its dashboard reporting; `/health` and `/ready` remain available without a heartbeat table.
- Auto-capture is on by default, but excludes casual chat, sensitive information, and content explicitly marked “don’t save.”
- A single user action maps to one atomic, idempotent write. Retry only transient SQLite busy/locked failures; never retry a completed write without its idempotency key.
- Set `MAX_TOOL_CALL_ROUNDS = 5`; this remains a safety cap, not a target number of tool calls.
- Never silently save a brain item. The existing execution loop, not model wording alone, must append a saved-item notice to every successful Telegram reply.
- Use `src/utils/errors.py` utilities for all exception handling; do not add direct `try`/`except` outside that module.
- All timestamps are timezone-aware UTC ISO 8601 text.
- Do not retain compatibility wrappers, aliases, no-op maintenance, or unused schema solely for a possible future caller; remove them once repository-wide references confirm they are unused.
- Do not commit code or documentation unless the user explicitly asks.

---

## Database before and after

### Current tables

| Table | Purpose | Decision |
| --- | --- | --- |
| `messages` | Retained chat history | Keep separate. |
| `memories` | Explicit stable facts/preferences | Migrate into `brain_items`, then remove. |
| `tasks` | Task definitions and recurrence | Migrate into `brain_items`, then remove. |
| `reminders` | Individual notification delivery attempts | Migrate/rename to `reminder_deliveries`. |
| `service_heartbeats` | Bot and worker liveness | Remove; it is not needed for the second brain or required health endpoints. |

Expense data remains in the external expense-tracker API, not in `roxy.db`.

### Target tables

```text
messages
  chat retention

brain_settings
  one local auto-capture setting

brain_items
  every meaningful personal item
        │
        ├──────────────────── reminder_deliveries
        │                         one row per notification attempt
        │
        └──────────────────── brain_items_fts
                                  SQLite full-text-search index
```

### `brain_settings`

| Field | Type | Why |
| --- | --- | --- |
| `id` | `INTEGER PRIMARY KEY CHECK (id = 1)` | Guarantees one local settings row. |
| `auto_capture_enabled` | `INTEGER NOT NULL` | Lets the user pause/resume natural capture. |
| `updated_at` | `TEXT NOT NULL` | Audits the setting change. |

### `brain_items`

| Field | Type | Why |
| --- | --- | --- |
| `id` | `INTEGER PRIMARY KEY` | Stable ID for search, graph nodes, commands, and reminders. |
| `content` | `TEXT NOT NULL` | Original thought, note, transcript, link, fact, or task text. |
| `title` | `TEXT NOT NULL` | Compact readable label. |
| `summary` | `TEXT NOT NULL` | Retrieval-friendly restatement of a longer item. |
| `item_type` | `TEXT NOT NULL` | `idea`, `fact`, `preference`, `person`, `project`, `goal`, `decision`, `task`, `reference`, or `reflection`. |
| `tags_json` | `TEXT NOT NULL` | Normalized JSON tag array for filters and graph edges. |
| `source_type` | `TEXT NOT NULL` | `text`, `voice`, `forwarded`, `photo_caption`, `link`, or `command`. |
| `source_url` | `TEXT` | Original saved URL, when present. |
| `capture_mode` | `TEXT NOT NULL` | `automatic`, `explicit`, or `command`. |
| `capture_key` | `TEXT UNIQUE` | Key derived from the Telegram chat and source-message IDs; prevents duplicate automatic saves after a retry. |
| `status` | `TEXT NOT NULL` | `active`, `archived`, `completed`, or `cancelled`. |
| `due_at` | `TEXT` | Next due time for `task` items; `NULL` for knowledge items. |
| `timezone` | `TEXT` | IANA timezone for a task; `NULL` otherwise. |
| `recurrence_rule` | `TEXT` | `daily`, `weekly:<day>`, or `monthly:<day>` for recurring tasks. |
| `created_at` | `TEXT NOT NULL` | Chronological review/export. |
| `updated_at` | `TEXT NOT NULL` | Edits, archiving, completion, and schedule changes. |
| `last_recalled_at` | `TEXT` | Future resurfacing of overlooked ideas. |

### `reminder_deliveries`

| Field | Type | Why |
| --- | --- | --- |
| `id` | `INTEGER PRIMARY KEY` | Identifies one delivery attempt. |
| `brain_item_id` | `INTEGER NOT NULL REFERENCES brain_items(id)` | Links the notification to its task brain item. |
| `scheduled_at` | `TEXT NOT NULL` | When it should be delivered. |
| `status` | `TEXT NOT NULL` | `pending`, `leased`, `delivered`, or `failed`. |
| `lease_expires_at`, `lease_token` | `TEXT` | Safe worker lease/recovery. |
| `attempt_count`, `last_error`, `delivered_at` | delivery fields | Retry and delivery history. |
| `created_at`, `updated_at` | `TEXT NOT NULL` | Ordering and operations audit. |

Create `brain_items_fts` as an FTS5 virtual table indexed from `brain_items.title`, `summary`, and `content`; maintain it with insert/update/delete triggers. Index `brain_items(status, created_at DESC)`, `brain_items(item_type, status)`, and `reminder_deliveries(status, scheduled_at)`.

## Thought map now, without vectors

The authenticated `/brain` page can render a useful 2D SVG map now:

- Each active `brain_items` row is a circle.
- `item_type` determines color; shared-tag count determines circle size.
- An edge connects items sharing tags, including a task connected to a project or idea.
- The accessible list below the SVG shows each title, summary, type, tags, and source URL.

This map shows **explicit relationships**. Later, embeddings can produce semantically weighted links and 3D placement, but must be added in a separate `brain_embeddings` table without changing this source schema.

## Execution phases and review gates

| Phase | Outcome | Stop and review before continuing |
| --- | --- | --- |
| **1. Data foundation** | Unified schema, safe migration, and unchanged reminder-worker behavior | Inspect the migrated database and confirm every existing memory/task/delivery is correct. |
| **2. Second-brain behavior** | Search, automatic capture, and user controls | Exercise Telegram capture/search/pause/delete flows and tune the capture rules. |
| **3. Visualize and release** | Authenticated thought map, documentation, and complete regression verification | Review the map with real saved items and approve the user experience before considering vectors. |

Do not begin a later phase until its review gate passes. At each gate, reassess this plan against the working code and user feedback; revise the remaining phase tasks before implementation resumes.

## Low-level runtime architecture

Second brain behavior uses the existing two layers only. It does not add a separate extraction service or a second post-reply LLM call.

```text
Telegram message / voice transcript
            │
            ▼
  chat handler + debounce + history
            │
            ▼
┌─────────────────────────────────────────┐
│ Layer 1: existing intent router          │
│ classify_tool_intent(messages, intents)  │
│                                         │
│ durable thought / brain request → brain  │
│ reminder request                → reminders
│ expense request                 → expenses
│ ordinary chat                   → general │
└─────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────┐
│ Layer 2: existing agent execution loop   │
│ ask_llm(messages, selected_tools)        │
│ → tool call → execute_tool_call(...)     │
│ → tool result → final Roxy reply         │
└─────────────────────────────────────────┘
            │
            ├─ brain tools → brain_items / reminder_deliveries
            ├─ reminder tools → task-shaped brain_items / deliveries
            ├─ expense tools → external expense API
            └─ general       → reply only
            ▼
      Telegram reply + history persistence
```

The intent router marks a durable statement as `brain` with `requires_tool = true`; the execution model then calls `save_brain_item`. Automatic versus explicit saving is passed as `capture_mode` and enforced by the brain tool against `brain_settings.auto_capture_enabled`.

## Phase 1: Data foundation and migration

### Task 1: Build unified storage and migrate existing local data

**Files:**
- Create: `src/utils/brain.py`
- Modify: `src/utils/memory.py`
- Modify: `src/utils/tasks.py`
- Delete: `src/utils/heartbeats.py`
- Modify: `src/app.py`
- Modify: `reminder_worker.py`
- Modify: `src/worker.py`
- Modify: `src/services/dashboard.py`
- Test: `tests/test_brain.py`
- Test: `tests/test_memory.py`
- Test: `tests/test_tasks.py`
- Modify: `tests/test_heartbeats.py`

**Interfaces:**
- Produces `BrainItem`, `create_item`, `get_item`, `search_items`, `list_recent_items`, `update_item`, `archive_item`, `complete_task_item`, `delete_item`, `auto_capture_enabled`, `set_auto_capture_enabled`, `export_brain_data`, and `brain_graph_data`.
- Produces `create_task_item(title, due_at, recurrence, timezone)`, `list_active_task_items`, and task update/complete helpers in `src/utils/tasks.py`; existing public Telegram behavior keeps its task-focused wording.

- [ ] **Step 1: Write failing storage and migration tests**

```python
def test_migration_moves_memory_into_a_fact_brain_item(self):
    memory.create_memory("My sister Anya lives in Pune.", kind="person")
    brain.initialize_schema()

    item = brain.search_items("Anya")[0]

    self.assertEqual((item.item_type, item.content), ("person", "My sister Anya lives in Pune."))

def test_migration_moves_task_and_delivery_to_unified_tables(self):
    task = tasks.create_task("Pay rent", "2099-01-01T09:00:00+05:30")
    brain.initialize_schema()

    item = brain.get_item_by_title("Pay rent")
    delivery = brain.list_deliveries_for_item(item.id)[0]
    self.assertEqual((item.item_type, delivery.brain_item_id), ("task", item.id))

def test_repeated_automatic_capture_key_creates_only_one_item(self):
    first = brain.create_item("Idea", "Idea", "An idea", "idea", [], "text", "automatic", capture_key="telegram:7:12:12")
    second = brain.create_item("Idea", "Idea", "An idea", "idea", [], "text", "automatic", capture_key="telegram:7:12:12")

    self.assertEqual((first.id, second.id, len(brain.list_recent_items())), (1, 1, 1))
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `uv run python -m unittest tests.test_brain -v`  
Expected: FAIL because `src.utils.brain` and the unified migration do not exist.

- [ ] **Step 3: Implement atomic schema creation and migration**

Within one SQLite transaction:

1. Create `brain_settings`, `brain_items`, `reminder_deliveries`, indexes, FTS table, and FTS triggers.
2. Insert the default settings row with `auto_capture_enabled = 1` if absent.
3. Copy every `memories` row into `brain_items` with its existing `kind` as `item_type`, `capture_mode = 'explicit'`, `status = 'active'`, source type `command`, and an empty JSON tag array.
4. Copy every `tasks` row into a new `brain_items` row with `item_type = 'task'`, task lifecycle status, `next_due_at` as `due_at`, and its timezone/recurrence.
5. Maintain an in-transaction old-task-ID → new-brain-item-ID mapping; use it to copy every `reminders` row into `reminder_deliveries`.
6. Drop `reminders`, `tasks`, `memories`, and `service_heartbeats` only after every row has copied successfully.

Schema initialization must be idempotent: once `brain_items` exists and legacy tables do not, it only ensures indexes, FTS triggers, and the settings row.

- [ ] **Step 4: Refactor existing utility callers**

Keep `src/utils/memory.py` only as the privacy-lifecycle module: its export and delete functions call `brain.export_brain_data()` and `brain.delete_all_brain_data()`. Replace memory CRUD imports in command and tool code with brain APIs. Refactor `src/utils/tasks.py` to create/update/list task-shaped `brain_items` and use `reminder_deliveries`; it must not create a `tasks` table.

Remove `src/utils/heartbeats.py`, heartbeat startup loops from `src/app.py` and `reminder_worker.py`, heartbeat calls from `src/worker.py`, and service-status reporting from `src/services/dashboard.py`. Keep `/health` as a process liveness endpoint and `/ready` as the existing database-availability check. Remove `tests/test_heartbeats.py` and update dashboard tests to assert no service-heartbeat section is returned.

- [ ] **Step 5: Run focused tests**

Run: `uv run python -m unittest tests.test_brain tests.test_memory tests.test_tasks -v`  
Expected: PASS.

### Task 2: Preserve reminder-worker delivery behavior on unified task items

**Files:**
- Modify: `src/worker.py`
- Modify: `src/tools/schedule_task.py`
- Modify: `src/tools/manage_tasks.py`
- Modify: `src/handlers/commands.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes task-shaped brain items and `reminder_deliveries` from Task 1.
- Produces exactly the existing scheduling, listing, updating, completion, recurrence, leasing, and retry behavior.

- [ ] **Step 1: Write failing worker regression tests**

```python
def test_worker_delivers_pending_reminder_for_a_task_brain_item(self):
    item = tasks.create_task_item("Pay rent", "2099-01-01T09:00:00+05:30", None, "Asia/Kolkata")
    delivery = brain.list_deliveries_for_item(item.id)[0]

    claimed = tasks.claim_due_reminders(now=delivery.scheduled_at)

    self.assertEqual(claimed[0].brain_item_id, item.id)
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `uv run python -m unittest tests.test_worker tests.test_commands -v`  
Expected: FAIL because worker/task queries still target legacy tables.

- [ ] **Step 3: Replace all legacy table queries**

Replace `tasks` joins with `brain_items WHERE item_type = 'task'`; replace every `reminders` query with `reminder_deliveries`. When a recurring delivery succeeds, update the task item's `due_at` and create its next `reminder_deliveries` row. When a task completes/cancels, update its brain-item status and fail only its pending/leased delivery rows. Preserve the current lease token semantics and all Telegram reply text.

- [ ] **Step 4: Run focused tests**

Run: `uv run python -m unittest tests.test_worker tests.test_tasks tests.test_commands -v`  
Expected: PASS.

### Phase 1 review gate

- [ ] Run `uv run python -m unittest discover -s tests -v`; all existing and new migration/scheduling tests pass.
- [ ] Open a disposable migrated database and confirm `messages`, `brain_settings`, `brain_items`, `reminder_deliveries`, and `brain_items_fts` are the only remaining application tables.
- [ ] Compare row counts and sampled records before/after migration: every memory is an equivalent brain item; every task is a task brain item; every reminder references the migrated item; recurring schedules still calculate correctly.
- [ ] Review the outcome with the user. Update this plan if the unified item fields or migration behavior need to change before starting Phase 2.

## Phase 2: Second-brain behavior

### Task 3: Add brain search, lifecycle tools, and automatic capture

**Files:**
- Create: `src/tools/brain.py`
- Modify: `src/tools/registry.py`
- Modify: `src/prompts/system.py`
- Test: `tests/test_brain.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Tools: `save_brain_item(content, title, summary, item_type, tags, capture_mode, source_url?)`, `search_brain(query, item_type?)`, `archive_brain_item(id)`, and `delete_brain_item(id)`.
- Intent: extend `TOOL_INTENTS` with `brain`; the existing `classify_tool_intent` returns this intent for durable statements as well as brain-management requests.

- [ ] **Step 1: Write failing tests for tool retrieval and safe auto-capture**

```python
async def test_durable_thought_selects_brain_tools(self):
    with patch("src.handlers.chat.classify_tool_intent", new=AsyncMock(return_value=("brain", True))):
        tools, choice = await chat.select_agent_tools([{"role": "user", "content": "Build a freelancer money app"}])

    self.assertEqual(choice, "required")
    self.assertIn("save_brain_item", {tool["function"]["name"] for tool in tools})

async def test_automatic_save_respects_the_pause_setting(self):
    brain.set_auto_capture_enabled(False)
    result = await brain_tools.save_brain_item('{"content":"Idea","title":"Idea","summary":"An idea","item_type":"idea","tags":[],"capture_mode":"automatic"}')
    self.assertFalse(result["ok"])
    self.assertEqual(brain.list_recent_items(), [])
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `uv run python -m unittest tests.test_brain tests.test_chat -v`  
Expected: FAIL because the brain intent and brain tools do not exist.

- [ ] **Step 3: Implement strict capture and tools**

Extend the existing intent-router prompt: classify durable ideas, facts, preferences, people, projects, goals, decisions, references, and reflections as the `brain` intent with tools required. Keep greetings, passwords/API keys, account numbers, health details, precise locations, expenses, and content containing “don’t save” as `general` with no brain tool. Reminder requests keep the `reminders` intent and become task brain items through Task 2.

In the system prompt for the existing execution loop, require `save_brain_item` for a `brain` intent: generate a short title, summary, supported item type, normalized tags, and `capture_mode = "automatic"` for inferred durable content or `"explicit"` when the user asks to save. The executor derives a `capture_key` from the current Telegram burst, refuses automatic saves when paused, but permits explicit saves. It retries only `sqlite3.OperationalError` busy/locked failures three times using the existing exponential `retry_async` utility. The SQLite transaction includes the item row and its FTS trigger update, so a failed transaction rolls back fully. Any final failure becomes an `{ "ok": false }` tool result; the agent must not claim the item was saved. When `save_brain_item` succeeds, `run_agent_loop` records the returned title and deterministically appends `Saved to your brain: <title>.` to the final Telegram text, even if the model did not mention it itself. If the fifth round is exhausted after a successful save, return `Saved to your brain: <title>. I couldn't finish the remaining request; please try that part again.` rather than the generic fallback.

- [ ] **Step 4: Run focused tests**

Run: `uv run python -m unittest tests.test_brain tests.test_chat -v`  
Expected: PASS.

### Task 4: Add user controls and privacy lifecycle

**Files:**
- Modify: `src/handlers/commands.py`
- Modify: `src/app.py`
- Modify: `README.md`
- Test: `tests/test_commands.py`
- Test: `tests/test_documentation.py`

**Interfaces:**
- Commands: `/brain`, `/brain_pause`, `/brain_resume`, `/brain_archive <id>`, `/brain_delete <id>`.

- [ ] **Step 1: Write failing command and privacy-lifecycle tests**

```python
def test_brain_pause_disables_capture(self):
    self.assertEqual(commands.brain_pause_response(), "Automatic brain capture is paused.")
    self.assertFalse(brain.auto_capture_enabled())
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `uv run python -m unittest tests.test_commands tests.test_documentation -v`  
Expected: FAIL because brain commands and lifecycle documentation do not exist.

- [ ] **Step 3: Implement commands and privacy lifecycle**

`/brain` lists the 20 newest active brain items. Pause/resume is idempotent. Archive/delete take one positive ID. `/export_data` adds `brain_items`, `brain_settings`, and `reminder_deliveries`; `/delete_data CONFIRM` removes all three plus retained messages. Document that a task is a brain item and task notifications have their own delivery history.

- [ ] **Step 4: Run focused tests**

Run: `uv run python -m unittest tests.test_commands tests.test_documentation -v`  
Expected: PASS.

### Phase 2 review gate

- [ ] Run `uv run python -m unittest discover -s tests -v`; all tests pass.
- [ ] In Telegram, test an idea, a stable fact, a task, a greeting, sensitive text, and “don’t save.” Confirm only appropriate meaningful content enters `brain_items`.
- [ ] Simulate a locked SQLite database and confirm the save retries three times, produces one item at most, and Roxy does not claim success if every attempt fails.
- [ ] Test `/brain`, `/brain_pause`, `/brain_resume`, archive, delete, export, and full delete against a disposable database.
- [ ] Review the saved items with the user. Adjust the intent-router and brain-tool prompts before Phase 3 if capture is too aggressive or too conservative.

## Phase 3: Visualize and release

### Task 5: Add the SQL-powered thought map

**Files:**
- Modify: `src/services/dashboard.py`
- Modify: `src/web.py`
- Create: `src/templates/brain.html`
- Test: `tests/test_dashboard.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Authenticated endpoints: `GET /brain` and `GET /brain-data`.

- [ ] **Step 1: Write failing map and authorization tests**

```python
def test_brain_map_links_items_sharing_a_tag(self):
    first = brain.create_item("Focus block", "Focus block", "Block afternoons", "goal", ["focus"], "text", "automatic")
    second = brain.create_item("No meetings", "No meetings", "Keep afternoons clear", "decision", ["focus"], "text", "automatic")
    self.assertEqual(brain.brain_graph_data()["edges"], [{"source": first.id, "target": second.id, "tags": ["focus"]}])

async def test_brain_page_requires_dashboard_login(self):
    response = await web.brain_page(unauthenticated_request)
    self.assertEqual(response.status_code, 303)
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `uv run python -m unittest tests.test_dashboard tests.test_web -v`  
Expected: FAIL because the brain snapshot and routes do not exist.

- [ ] **Step 3: Implement the authenticated SVG map**

`/brain-data` returns only active brain items and shared-tag edges, never `messages`. `/brain` renders a deterministic 900×620 SVG with edges first, type-colored circles next, and an accessible detail list beneath it. Require the existing dashboard login for both endpoints.

- [ ] **Step 4: Run focused tests**

Run: `uv run python -m unittest tests.test_dashboard tests.test_web -v`  
Expected: PASS.

### Task 6: Full regression and release verification

**Files:**
- Modify only to correct verified defects from earlier tasks.

- [ ] **Step 1: Run the full suite**

Run: `uv run python -m unittest discover -s tests -v`  
Expected: PASS.

- [ ] **Step 2: Verify a legacy populated database**

Create a disposable database containing a memory, a one-time task, and a recurring task with delivery history. Start Roxy once, then verify: `memories`, `tasks`, and `reminders` no longer exist; all information is represented by `brain_items` and `reminder_deliveries`; schedules and task status are unchanged.

- [ ] **Step 3: Verify privacy and map behavior**

Send one durable idea, one casual greeting, and one “don’t save” message. Confirm only the idea appears in `/brain`, exported brain data, and the map. Confirm a task and its connected project share a tag and therefore have a visible map edge.

### Phase 3 review gate

- [ ] Run `uv run python -m unittest discover -s tests -v`; all tests pass.
- [ ] Open `/brain` using real saved items and confirm the node labels, colors, edges, isolated notes, and accessible list are understandable and contain no raw chat history.
- [ ] Confirm health/readiness endpoints still work after heartbeat removal, and that export/delete cover the complete local data lifecycle.
- [ ] Review the complete second brain with the user. Only after approval, decide whether semantic search and the future 3D vector map solve a real retrieval problem.

## Future vector/3D migration (out of scope)

If semantic search becomes necessary, add `brain_embeddings(brain_item_id INTEGER PRIMARY KEY REFERENCES brain_items(id), model TEXT NOT NULL, vector_json TEXT NOT NULL, created_at TEXT NOT NULL)`. Keep `brain_items` as the source of truth. Combine FTS, tags, and semantic similarity for retrieval; use similarity only to weight links and 3D placement, while tags remain the human-readable explanation for a connection.
