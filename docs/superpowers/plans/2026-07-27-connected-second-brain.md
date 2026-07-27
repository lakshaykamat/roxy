# Connected Second Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a time-aware, connected Roxy second brain that analyzes explicit saves, ingests public links, researches the web naturally, and exposes an accessible 2D grouped Brain explorer.

**Architecture:** `brain_items` remains the FTS-searchable atomic knowledge store. New capture and relation tables preserve the original save request, grouped child items, provenance, timestamps, and sparse explainable relationships. Link capture and web research are independent adapters; the tool router exposes only the small relevant tool set. `/brain` consumes a richer authenticated snapshot and offers safe item lifecycle actions.

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

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/knowledge/link_capture.py` | Safe URL normalization/fetching and readable source extraction. |
| `src/knowledge/captures.py` | Capture planning and small capture/result dataclasses. |
| `src/knowledge/web_search.py` | Provider web-search adapter and normalized cited results. |
| `src/knowledge/brain.py` | Migrations, capture/item/relation persistence, timeline and query APIs. |
| `src/knowledge/brain_tools.py` | Capture, search, archive, delete tool definitions and executors. |
| `src/agent/tool_registry.py`, `src/core/llm.py`, `src/prompts/system.py` | Narrow, clean intent selection and model instructions. |
| `src/web.py`, `src/dashboard/service.py`, `src/templates/brain.html` | Authenticated Brain snapshot, lifecycle endpoints, and explorer UI. |
| `tests/test_link_capture.py`, `tests/test_web_search.py` | New isolated network/provider tests. |
| `tests/test_brain.py`, `tests/test_chat.py`, `tests/test_web.py` | Storage, agent, and UI endpoint regression tests. |

## Phase 1: Capture, links, and natural web research

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

### Task 2: Persist explicit captures and atomic items

**Files:** Modify `src/knowledge/brain.py`; create `src/knowledge/captures.py`; modify `tests/test_brain.py`.

**Interfaces:** Produce `plan_capture(request: str, sources: list[CapturedSource]) -> CapturePlan`; `create_capture(plan: CapturePlan) -> Capture`; `create_relation(source_id: int, target_id: int, relation_type: str, explanation: str, confidence: float, origin: str) -> None`; `brain_timeline(limit: int) -> list[dict[str, object]]`.

- [ ] **Step 1: Add failing migration tests.**

```python
def test_compound_capture_keeps_parent_and_two_searchable_children(self):
    capture = brain.create_capture(plan_with_two_items())
    self.assertEqual(len(brain.items_for_capture(capture.id)), 2)
    self.assertEqual(brain.search_items("first insight")[0].title, "First insight")

def test_relation_is_merged_not_duplicated(self):
    brain.create_relation(1, 2, "supports", "same project evidence", .8, "inferred")
    brain.create_relation(1, 2, "supports", "same project evidence", .8, "inferred")
    self.assertEqual(len(brain.relations_for_item(1)), 1)
```

- [ ] **Step 2: Run `python -m unittest tests.test_brain -v`; expect failures for new APIs.**
- [ ] **Step 3: Extend `_initialize_schema` idempotently with `brain_captures`, `brain_capture_items`, `brain_item_relations`, `source_published_at` on `brain_items`, indexes on capture time and relation endpoints, and migration-safe `PRAGMA table_info` checks.** Persist `captured_at`, original request, concise overall analysis, and rationale. Add unique relation key `(source_item_id, target_item_id, relation_type)` and update existing evidence/confidence rather than insert duplicates.
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

## Phase 2: Connected timeline and recall

### Task 5: Build sparse explainable relationships and recall context

**Files:** Modify `src/knowledge/captures.py`, `src/knowledge/brain.py`, `src/knowledge/brain_tools.py`; modify `tests/test_brain.py`.

- [ ] **Step 1: Write tests proving shared tags alone create no stored relation, while an explicit project/source relationship does.**
- [ ] **Step 2: Implement relation inference after each capture using only planner-provided evidence; allow `about`, `supports`, `contradicts`, `continues`, `decides`, `updates`, and `source_for`; reject blank explanations and confidence outside `0..1`.**
- [ ] **Step 3: Extend `search_brain` results with `source_url`, `captured_at`, `source_published_at`, parent capture summary, and relations. Update `brain_context` to include concise source and relation context.**
- [ ] **Step 4: Run `python -m unittest tests.test_brain tests.test_chat -v`; expect PASS.**

## Phase 3: Brain explorer and lifecycle controls

### Task 6: Expose authenticated explorer data and safe actions

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

### Task 7: Redesign the Brain page around a 2D grouped timeline, connections, and items

**Files:** Modify `src/templates/brain.html`, `src/web.py`; modify `tests/test_web.py`.

- [ ] **Step 1: Add rendering tests for `TIMELINE`, `CONNECTIONS`, `SAVED_ITEMS`, relation explanation text, local-time elements, safe `https` links, and confirmation-delete form attributes.**
- [ ] **Step 2: Replace the slate-only map template with the dashboard's `paper/panel/ink/line/accent/alert` theme, shared header/sign-out form, skip link, monospace typography, and record-card styling.**
- [ ] **Step 3: Render a timeline card list, searchable/filterable item list, and a labeled 2D relation view grouped by active theme/project and date. Show the selected item and its direct neighbors with a selected-item detail panel, rather than every note as an unstructured dot cloud. Every edge label and explanation must also appear as text in the item details. On narrow screens show timeline/items first and place the explorer below. Do not add a 3D canvas, WebGL dependency, or 3D interaction.**
- [ ] **Step 4: Add small browser JavaScript to filter items, select a relation node, call archive/delete endpoints with credentials, and open an in-page explicit delete confirmation. Handle 409/404/503 with visible status text.**
- [ ] **Step 5: Run `python -m unittest tests.test_web -v`; manually verify `/brain` at mobile and desktop widths, keyboard navigation, source links, archive, and deletion confirmation.**

### Task 8: Complete regression and documentation

**Files:** Modify `README.md`, `.env.example`; all affected test modules.

- [ ] **Step 1: Document public-link limits, manual-description fallback, natural web research, explicit-save behavior, source citations, Brain timeline/connections, archive, and confirmation-required deletion.** Document any configured web-search model/provider setting without example secrets.
- [ ] **Step 2: Run `python -m unittest discover -s tests -v`; expect PASS.**
- [ ] **Step 3: Run `git diff --check` and `git status --short`; verify no `.env`, database, or unrelated `todo.todo` user changes are staged or altered.**

## Plan self-review

- Capture and web requirements: Tasks 1–4.
- Timeline, provenance, and sparse explainable relationships: Tasks 2 and 5.
- Dashboard-consistent, accessible UI and lifecycle actions: Tasks 6–7.
- Privacy, configuration, docs, and complete regression: Task 8.
- No raw model reasoning, auto-saved web results, crawler/index work, or unconfirmed deletion is included.
