# Connected Second Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a time-aware connected Roxy second brain that analyzes every Brain write, creates sparse evidence-backed and clearly labeled inferred links, introspects recent/unconnected thoughts daily at 3:00 AM, and exposes an accessible explorer.

**Architecture:** `brain_items` remains the FTS-searchable atomic knowledge store. A shared analysis service normalizes every explicit and automatic write before it reaches the store; it compares the new record with active items and emits direct or strict-confidence inferred relations. The reminder worker also invokes a bounded 3:00 AM `TASK_TIMEZONE` introspection pass for recent and unconnected items. `/brain` reads stored relations only—display grouping is never treated as a connection.

**Tech Stack:** Python 3.14, SQLite/FTS5, `httpx`, OpenAI Responses web-search tool, `python-telegram-bot`, FastAPI, Tailwind CDN, standard-library `unittest`.

## Global Constraints

- Public `http`/`https` sources only; reject private, loopback, link-local, multicast, reserved, and redirect-to-private hosts before requesting them.
- Never create a crawler, search index, or source authentication flow. Use the provider web-search tool for natural internet research.
- Explicit `save this` / `remember this` writes immediately. Ask one question only when material is ambiguous or an unreadable source needs a manual description. Only permanent deletion needs confirmation.
- Store concise analysis, evidence, provenance, and capture rationale, never model chain-of-thought or hidden scratch work.
- Store timestamps as UTC ISO 8601; display them in `TASK_TIMEZONE`; source publication time is optional and distinct from capture time.
- Never auto-save web-search results; save only after an explicit user request.
- Use `src/core/errors.py`; do not add direct `try`/`except` outside that module.
- Preserve user changes in the dirty worktree and do not commit unless the user explicitly asks.
- Every explicit and automatic Brain write uses the shared analysis service. If model analysis fails, persist one minimal unconnected item; never discard an explicit save.
- Store normalized entity/domain tags and only create `same entity`, `same domain`, or `related topic (inferred)` relations with a concise user-readable explanation. Treat semantic links as inferred only at or above a strict confidence threshold.
- The 3:00 AM introspection job considers active items from the last 30 days plus all active unconnected items. It creates, refreshes, downgrades, or removes inferred relations only; it never creates synthetic summary notes.
- This is a clean-database change: remove legacy migration and compatibility code. Do not backfill, reinterpret, or preserve legacy tables.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/knowledge/link_capture.py` | Safe URL normalization/fetching and readable source extraction. |
| `src/knowledge/captures.py` | Capture planning and small capture/result dataclasses. |
| `src/knowledge/web_search.py` | Provider web-search adapter and normalized cited results. |
| `src/knowledge/brain.py` | Migrations, capture/item/relation persistence, timeline and query APIs. |
| `src/knowledge/brain_tools.py` | Capture, search, archive, delete tool definitions and executors. |
| `src/knowledge/brain_analysis.py` | Strict structured analysis, tag normalization, direct-match detection, and safe relation-candidate validation for all Brain writes. |
| `src/knowledge/introspection.py` | Bounded nightly selection and relation refresh orchestration. |
| `src/agent/tool_registry.py`, `src/core/llm.py`, `src/prompts/system.py` | Narrow, clean intent selection and model instructions. |
| `src/web.py`, `src/dashboard/service.py`, `src/templates/brain.html` | Authenticated Brain snapshot, lifecycle endpoints, and explorer UI. |
| `tests/test_link_capture.py`, `tests/test_web_search.py` | New isolated network/provider tests. |
| `tests/test_brain.py`, `tests/test_chat.py`, `tests/test_web.py` | Storage, agent, and UI endpoint regression tests. |
| `tests/test_brain_analysis.py`, `tests/test_worker.py` | Analysis fallback/direct/inferred relation coverage and 3:00 AM scheduling coverage. |

## Phase 1: Analyze every save and create immediate thought connections

**Outcome:** Every explicit and automatic Brain write is normalized before storage and connected to clearly related active thoughts. Link capture, research, clean-database setup, and recall all use the same trustworthy relation model.

### Task 1: Add safe public-source extraction

**Files:** Create `src/knowledge/link_capture.py`; create `tests/test_link_capture.py`; modify `src/config.py`.

**Interfaces:** Produce `CapturedSource(url: str, title: str | None, text: str | None, published_at: str | None, status: Literal["analyzed", "manual_description", "bookmark"])` and `async capture_public_url(url: str) -> CapturedSource`.

- [ ] **Step 1: Write failing URL-safety and extraction tests.**

```python
async def test_private_destination_is_not_requested(self):
    with patch("src.knowledge.link_capture.resolve_public_host", return_value=False):
        result = await capture_public_url("http://127.0.0.1/private")
    self.assertEqual(result.status, "bookmark")

async def test_html_source_returns_title_and_visible_text(self):
    response = httpx.Response(200, headers={"content-type": "text/html"}, text="<title>Roadmap</title><article>Useful text</article>")
    with patch("src.knowledge.link_capture.fetch", new=AsyncMock(return_value=response)):
        result = await capture_public_url("https://example.com")
    self.assertEqual((result.title, result.text), ("Roadmap", "Useful text"))
```

- [ ] **Step 2: Run `python -m unittest tests.test_link_capture -v`; expect import failure.**
- [ ] **Step 3: Implement `normalize_public_url`, DNS/IP validation, redirect revalidation, 10-second timeout, 1 MiB byte limit, and an `HTMLParser` extractor for title/meta description/article text.** Use `httpx.AsyncClient(follow_redirects=False)` and return `manual_description` for a valid but unreadable source with no informative metadata.
- [ ] **Step 4: Run the focused test; expect PASS.**

### Task 2: Persist atomic captures and remove legacy migration behavior

**Files:** Modify `src/knowledge/brain.py`; create `src/knowledge/captures.py`; modify `tests/test_brain.py`.

**Interfaces:** Produce `plan_capture(request: str, sources: list[CapturedSource]) -> CapturePlan`; `create_capture(plan: CapturePlan) -> Capture`; `create_relation(source_id: int, target_id: int, relation_type: str, explanation: str, confidence: float, origin: str) -> None`; `brain_timeline(limit: int) -> list[dict[str, object]]`.

- [ ] **Step 1: Replace legacy-migration coverage with clean-database persistence tests.**

```python
def test_compound_capture_keeps_parent_and_two_searchable_children(self):
    capture = brain.create_capture(plan_with_two_items())
    self.assertEqual(len(brain.items_for_capture(capture.id)), 2)
    self.assertEqual(brain.search_items("first insight")[0].title, "First insight")

def test_relation_is_merged_not_duplicated(self):
    brain.create_relation(1, 2, "supports", "same project evidence", .8, "inferred")
    brain.create_relation(1, 2, "supports", "same project evidence", .8, "inferred")
    self.assertEqual(len(brain.relations_for_item(1)), 1)

def test_initialization_does_not_read_or_drop_legacy_tables(self):
    with sqlite3.connect(config.DATABASE_PATH) as connection:
        connection.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        connection.execute("INSERT INTO memories (content) VALUES ('old data')")
    brain_store.initialize_schema()
    with sqlite3.connect(config.DATABASE_PATH) as connection:
        self.assertEqual(connection.execute("SELECT content FROM memories").fetchone()[0], "old data")
```

- [ ] **Step 2: Run `python -m unittest tests.test_brain -v`; expect failures for new APIs.**
- [ ] **Step 3: Keep `_initialize_schema` idempotent for the clean Brain schema, including `brain_captures`, `brain_capture_items`, `brain_item_relations`, `source_published_at` on `brain_items`, and indexes on capture time and relation endpoints. Delete `_tables`, `_migrate_legacy_data`, and the `migrate_legacy_data` parameter from `_brain_database`; callers initialize only Brain-owned tables.** Persist `captured_at`, original request, concise overall analysis, and rationale. Add unique relation key `(source_item_id, target_item_id, relation_type)` and update existing evidence/confidence rather than insert duplicates.
- [ ] **Step 4: Implement a conservative planner: one child for a simple thought; one child per distinct URL or independently retrievable concept; one overview child for context that cannot safely split.** Planner output contains only user-facing rationale/evidence, never hidden reasoning.
- [ ] **Step 5: Run focused tests; expect PASS.**

### Task 3: Add explicit capture and web-search tools

**Files:** Modify `src/knowledge/brain_tools.py`, `src/agent/tool_registry.py`, `src/prompts/system.py`; create `src/knowledge/web_search.py`; create `tests/test_web_search.py`.

**Interfaces:** Add `capture_brain_content(arguments: str) -> dict[str, object]` and `search_web(arguments: str) -> dict[str, object]`; return `{"ok": True, "results": [{"title": str, "url": str, "summary": str}]}` from web search.

- [ ] **Step 1: Write failing tool tests.**

```python
async def test_web_search_returns_cited_results_without_persisting(self):
    result = await search_web('{"query":"recent SQLite FTS5 guide"}')
    self.assertTrue(result["ok"])
    self.assertIn("url", result["results"][0])
    self.assertEqual(brain.list_recent_items(), [])

async def test_direct_capture_does_not_require_confirmation(self):
    result = await capture_brain_content('{"request":"save this idea", "urls": []}')
    self.assertTrue(result["ok"])
```

- [ ] **Step 2: Run the two focused modules; expect missing imports/APIs.**
- [ ] **Step 3: Implement `search_web` with the OpenAI Responses API `tools=[{"type": "web_search"}]`, normalize citations/URLs into the stated result schema, and use `try_async` to return a short provider-unavailable error.** Do not add a crawler or save results.
- [ ] **Step 4: Implement capture tool batch behavior: process every distinct URL; save successes even if another URL is unreadable; return `needs_description` plus one question when required; otherwise save an analyzed reference or bookmark immediately.**
- [ ] **Step 5: Update prompt rules so research requires citations and save commands do not trigger a second confirmation. Run focused tests; expect PASS.**

### Task 4: Simplify intent routing and chat notices

**Files:** Modify `src/core/llm.py`, `src/agent/tool_registry.py`, `src/handlers/chat.py`; modify `tests/test_chat.py`.

- [ ] **Step 1: Add failing routing tests for `web_research`, `brain_capture`, `brain_management`, and ordinary chat.**
- [ ] **Step 2: Replace the `brain` intent with `brain_capture`; add `web_research`; make each intent map to exactly its required tool names.** Add router examples for “find current…” and “save these links…”, and assert `tool_definitions_for_intent("web_research") == [WEB_SEARCH_DEFINITION]`.
- [ ] **Step 3: Change `run_agent_loop` to collect all successful capture titles/URLs and append one truthful result notice, rather than only the final saved title. Preserve failure and round-limit behavior.**
- [ ] **Step 4: Run `python -m unittest tests.test_chat tests.test_brain tests.test_web_search -v`; expect PASS.**

### Task 5: Analyze every Brain write and persist explainable thought connections

**Files:** Create `src/knowledge/brain_analysis.py`; modify `src/core/llm.py`, `src/knowledge/capture_planner.py`, `src/knowledge/tools.py`, `src/knowledge/brain_store.py`; create `tests/test_brain_analysis.py`; modify `tests/test_brain_store.py`.

**Interfaces:** Produce `BrainAnalysis(title: str, summary: str, item_type: str, tags: list[str])`, `RelationCandidate(target_item_id: int, relation_type: Literal["same entity", "same domain", "related topic (inferred)"], explanation: str, confidence: float, origin: Literal["direct", "inferred"])`, `async analyze_brain_item(content: str, item_type: str) -> BrainAnalysis | None`, and `async analyze_and_save_item(...) -> BrainItem`. A valid result stores only user-facing metadata, never hidden reasoning.

- [ ] **Step 1: Write failing analysis tests before implementation.**

```python
class BrainAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_ruchi_records_create_a_direct_same_entity_relation(self):
        first = await analyze_and_save_item("My girlfriend is Ruchi.", "idea", "explicit")
        second = await analyze_and_save_item("Ruchi's birthday is 17 June 2005.", "idea", "explicit")

        relation = brain_store.list_item_relations(second.id)[0]
        self.assertEqual(relation["relation_type"], "same entity")
        self.assertEqual(relation["origin"], "direct")
        self.assertEqual(relation["explanation"], "Both records refer to Ruchi.")

    async def test_semantic_link_requires_the_strict_threshold(self):
        existing = brain_store.save_item("Prepare for interview", "Interview", "Prepare", "goal", ["domain:career"], "text", "explicit")
        with patch("src.knowledge.brain_analysis.ask_brain_analysis", return_value=semantic_analysis(confidence=.74)):
            saved = await analyze_and_save_item("Practice concise interview answers", "idea", "explicit")
        self.assertEqual(brain_store.list_item_relations(saved.id), [])

    async def test_analysis_failure_saves_a_minimal_unconnected_record(self):
        with patch("src.knowledge.brain_analysis.ask_brain_analysis", side_effect=OSError("offline")):
            saved = await analyze_and_save_item("Ruchi's birthday is 17 June 2005.", "idea", "explicit")
        self.assertEqual((saved.title, saved.tags), ("Ruchi's birthday is 17 June 2005.", []))
        self.assertEqual(brain_store.list_item_relations(saved.id), [])
```

- [ ] **Step 2: Run `python -m unittest tests.test_brain_analysis -v`; expect `ModuleNotFoundError: src.knowledge.brain_analysis`.**
- [ ] **Step 3: Implement `ask_brain_analysis` in `src/knowledge/brain_analysis.py` with `OPENAI_MODEL` and a strict JSON schema.** The schema must require `title`, `summary`, `item_type`, `entities`, and `domains`; reject unknown item types, blank strings, more than 12 tags, and tags that do not use normalized `entity:<lowercase-name>` or `domain:<lowercase-topic>` forms. Use `try_async` to return `None` for model, transport, or schema failures. The prompt must request concise user-visible fields and explicitly prohibit hidden reasoning.
- [ ] **Step 4: Implement direct matching and semantic candidates.** Compare normalized tags against active records: shared `entity:*` yields `same entity` and shared non-generic `domain:*` yields `same domain`, each with a deterministic explanation. For records without direct overlap, send only the new record and bounded candidate metadata (ID, title, summary, tags) to the relation schema; accept `related topic (inferred)` only when `confidence >= 0.85`, the target ID is among candidates, and its explanation is non-empty. De-duplicate symmetric pairs by consistently saving the lower ID as source.
- [ ] **Step 5: Add `save_analyzed_item` and `save_analyzed_capture` orchestration.** Both must call analysis before `brain_store.save_item`/`save_capture`, then persist accepted `RelationCandidate` records in the same database transaction as the new items. Route `tools.save_brain_item` and `tools.capture_brain_content` through these functions so automatic and explicit writes cannot bypass analysis. Preserve automatic-capture pause and lock-retry behavior.
- [ ] **Step 6: Extend `RELATION_TYPES` with `same entity`, `same domain`, and `related topic (inferred)`; retain existing source/planner relation types.** Extend search/context serialization with relation origin and confidence. Remove tag-overlap edge fabrication from `get_brain_graph`; read `brain_item_relations` instead.
- [ ] **Step 7: Run `python -m unittest tests.test_brain_analysis tests.test_brain_store tests.test_web_research -v`; expect PASS.**

### Task 6: Refresh eligible thought connections at 3:00 AM

**Files:** Create `src/knowledge/introspection.py`; modify `src/reminders/worker.py`, `src/knowledge/brain_store.py`, `src/config.py`; modify `tests/test_worker.py`; create `tests/test_introspection.py`.

**Interfaces:** Produce `eligible_for_introspection(now: datetime) -> list[BrainItem]`, `async refresh_brain_connections(now: datetime) -> int`, `next_introspection_at(now: datetime, timezone_name: str) -> datetime`, and `ReminderWorker.run_introspection_if_due(now: datetime | None = None) -> bool`.

- [ ] **Step 1: Write failing scheduler and selection tests.**

```python
def test_next_introspection_is_three_am_in_task_timezone(self):
    now = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)
    run_at = next_introspection_at(now, "Asia/Kolkata")
    self.assertEqual(run_at.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M"), "03:00")

def test_eligible_items_include_recent_and_unconnected_active_records(self):
    recent = save_at("Recent thought", days_ago=2, connected=True)
    old_unconnected = save_at("Old disconnected thought", days_ago=90, connected=False)
    old_connected = save_at("Old connected thought", days_ago=90, connected=True)
    self.assertEqual({item.id for item in eligible_for_introspection(utc_now())}, {recent.id, old_unconnected.id})

async def test_nightly_refresh_never_creates_a_synthetic_brain_item(self):
    before = len(brain_store.list_recent_items(100))
    await refresh_brain_connections(utc_now())
    self.assertEqual(len(brain_store.list_recent_items(100)), before)
```

- [ ] **Step 2: Run `python -m unittest tests.test_introspection tests.test_worker -v`; expect missing imports and methods.**
- [ ] **Step 3: Implement bounded eligibility and refresh.** `eligible_for_introspection` must return active records created in the preceding 30 days plus active records having no relation rows, capped at 100 records. Process batches of at most 20 candidate items. Reuse the Task 5 candidate validation and threshold; direct relations may be inserted/refreshed, while inferred relations for an evaluated pair are updated, deleted when unsupported, or left untouched when the pair was outside the batch. Never alter item content/title/summary, create a Brain item, or send Telegram messages.
- [ ] **Step 4: Add a single scheduler hook to `ReminderWorker.run`.** Use `next_introspection_at(now, TASK_TIMEZONE)` and await the current scheduled run before calculating the next one. Keep reminder polling responsive by running `run_introspection_if_due` as a separate task and logging failures through `try_async`; an unavailable model/database must not stop reminder delivery. Do not add another long-running process or an external scheduler dependency.
- [ ] **Step 5: Run `python -m unittest tests.test_introspection tests.test_worker -v`; expect PASS.**

## Phase 2: Refine thought connections nightly and expose them clearly

**Outcome:** At 3:00 AM, Roxy fills in safe missed connections among recent and previously disconnected thoughts. The Brain page then visualizes actual stored links rather than grouping records by date or type.

### Task 7: Expose authenticated explorer data and safe actions

**Files:** Modify `src/dashboard/service.py`, `src/web.py`; modify `tests/test_web.py`.

- [ ] **Step 1: Write failing endpoint tests.**

```python
def test_delete_requires_named_confirmation(self):
    response = self.authenticated_client().post("/brain/items/7/delete", json={"confirmed": False})
    self.assertEqual(response.status_code, 409)

def test_archive_updates_active_brain_snapshot(self):
    response = self.authenticated_client().post("/brain/items/7/archive")
    self.assertEqual(response.status_code, 200)
```

- [ ] **Step 2: Create `get_brain_snapshot()` with timeline captures, item metadata, analyzed/bookmark state, source links, and relation explanations.**
- [ ] **Step 3: Replace graph-only `/brain-data` output with this snapshot; add authenticated POST archive/delete endpoints.** Delete body must include `{"confirmed": true, "title": "exact item title"}` and return 409 until both match the stored active item. Use existing brain lifecycle APIs and return 404 for missing IDs.
- [ ] **Step 4: Run `python -m unittest tests.test_web -v`; expect PASS.**

### Task 8: Render stored thought connections instead of type/date groups

**Files:** Modify `src/templates/brain.html`, `src/web.py`; modify `tests/test_web.py`.

- [ ] **Step 1: Add rendering tests for `TIMELINE`, `CONNECTIONS`, `SAVED_ITEMS`, relation explanation text, local-time elements, safe `https` links, and confirmation-delete form attributes.**
- [ ] **Step 2: Replace the slate-only map template with the dashboard's `paper/panel/ink/line/accent/alert` theme, shared header/sign-out form, skip link, monospace typography, and record-card styling.**
- [ ] **Step 3: Render a timeline card list, searchable/filterable item list, and a labeled 2D relation view from stored relation endpoints only. Do not form connection groups from item type, first tag, or capture date. A layout cluster may use a `domain:*` tag for readability, but label it “domain grouping” and never imply that it is a relationship. Show the selected item and its actual direct neighbors with a detail panel. Every relation must display `same entity`/`same domain`/`related topic (inferred)`, origin, confidence, and explanation as text. On narrow screens show timeline/items first and place the explorer below. Do not add a 3D canvas, WebGL dependency, or 3D interaction.**
- [ ] **Step 4: Add small browser JavaScript to filter items, select a relation node, call archive/delete endpoints with credentials, and open an in-page explicit delete confirmation. Handle 409/404/503 with visible status text.**
- [ ] **Step 5: Run `python -m unittest tests.test_web -v`; manually verify `/brain` at mobile and desktop widths, keyboard navigation, source links, archive, and deletion confirmation.**

### Task 9: Complete regression and documentation

**Files:** Modify `README.md`, `.env.example`; all affected test modules.

- [ ] **Step 1: Document public-link limits, manual-description fallback, natural web research, analysis of explicit and automatic Brain saves, direct versus inferred connection labels, the 3:00 AM `TASK_TIMEZONE` introspection scope, source citations, archive, and confirmation-required deletion.** Document any configured web-search model/provider setting without example secrets.
- [ ] **Step 2: Run `python -m unittest discover -s tests -v`; expect PASS.**
- [ ] **Step 3: Run `git diff --check` and `git status --short`; verify no `.env`, database, or unrelated `todo.todo` user changes are staged or altered.**

## Plan self-review

- Capture and web requirements: Tasks 1–4.
- Analysis on every Brain write, direct/inferred stored connections, and clean-database behavior: Tasks 2 and 5.
- 3:00 AM recent/unconnected-item introspection with no synthetic notes: Task 6.
- Dashboard-consistent, accessible relation rendering and lifecycle actions: Tasks 7–8.
- Privacy, configuration, docs, and complete regression: Task 9.
- No raw model reasoning, type/date grouping-as-relations, auto-saved web results, crawler/index work, synthetic nightly memories, or unconfirmed deletion is included.
