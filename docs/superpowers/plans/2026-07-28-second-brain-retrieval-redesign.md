# Simple Second-Brain Retrieval Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Roxy reliably save and retrieve personal thoughts and links with one simple Brain table, one shared retrieval service, and no implicit memory injection into chat.

**Architecture:** Keep `brain_items` as the only Brain storage table. Each saved link remains one `brain_items` record: its URL stays in `source_url`, extracted page text stays in `content`, and one new `source_status` field distinguishes analyzed pages from bookmarks/unavailable pages. A small retrieval module combines the existing item FTS search with parameterized URL and tag matching; Telegram and `/brain` both use that result.

**Tech Stack:** Python 3.14, SQLite FTS5, FastAPI, python-telegram-bot, OpenAI tool calling, standard-library `unittest`.

## Global Constraints

- Do not create `brain_sources`, `brain_source_evidence`, `brain_item_sources`, `brain_item_tags`, or any additional Brain FTS table.
- Preserve every existing `brain_items`, capture, relation, reminder, and setting row.
- Add only nullable `brain_items.source_status TEXT` through an idempotent startup migration. Populate `legacy` only for existing non-empty `source_url` rows.
- Keep one link per Brain item. Capturing several URLs creates several reference items, as it does today.
- Keep `content` as the durable user thought or extracted readable-page text; do not overwrite it with model reasoning or later fetches.
- Preserve `source_url` exactly as supplied by the reader. Do not add canonical URL, redirect-history, or source-snapshot tables.
- An explicit public URL must create a Brain item even if it cannot be read.
- Use parameterized SQLite statements only. Build FTS queries from literal escaped tokens; never insert raw user text into `MATCH` syntax.
- Search excludes archived records. Blank or filler-only queries return no records.
- Recall happens only through `search_saved_items`; normal text and photo messages must not query the Brain automatically.
- Store concise user-visible metadata and relation explanations only. Never store chain-of-thought or hidden scratch work.
- Use `src/core/errors.py` utilities for exception handling. Do not add dependencies or create a commit unless the user explicitly requests it.

## Resulting Data Model

```text
brain_items
  id, content, title, summary, item_type, tags_json,
  source_type, source_url, source_published_at, source_status,
  capture_mode, capture_key, status, created_at, updated_at,
  last_recalled_at, last_organized_at, completed_at, task fields

brain_captures, brain_capture_items, brain_item_relations,
brain_settings, reminder_deliveries

brain_items_fts
  title, summary, content
```

`source_status` is `NULL` for non-link thoughts and otherwise one of `analyzed`, `bookmark`, `unavailable`, or `legacy`:

- `analyzed`: the public page produced usable text or a title.
- `bookmark`: the user saved a URL without reading it.
- `unavailable`: Roxy tried and could not read the public URL.
- `legacy`: an existing record created before this change.

## File Structure

- `src/knowledge/brain_store.py`: migration, `BrainItem` mapping, simple URL/tag retrieval helpers, and paged active-item listing.
- `src/knowledge/capture_planner.py`: creates a reference item for every supplied URL, including unavailable URLs.
- `src/knowledge/brain_analysis.py`: passes source status through the existing atomic capture write and keeps analysis metadata separate from content.
- `src/knowledge/retrieval.py`: literal query normalization and merging of FTS, URL, and tag matches.
- `src/knowledge/tools.py`: returns shared retrieval results and updates recall timestamps after successful explicit recall.
- `src/handlers/chat.py`, `src/prompts/system.py`: remove implicit text/photo Brain lookup and define tool-only recall behavior.
- `src/dashboard/service.py`, `src/web.py`, `src/templates/brain.html`: display source status, use shared search, and browse active records in simple offset pages.
- `tests/test_brain_store.py`, `tests/test_brain_analysis.py`, `tests/test_retrieval.py`, `tests/test_chat.py`, `tests/test_dashboard.py`, `tests/test_web.py`: focused regression tests.

---

### Task 1: Add the one-column migration and preserve every explicit URL

**Files:**
- Modify: `src/knowledge/brain_store.py`
- Modify: `src/knowledge/capture_planner.py`
- Modify: `src/knowledge/brain_analysis.py`
- Test: `tests/test_brain_store.py`
- Test: `tests/test_brain_analysis.py`

**Interfaces:**
- Extends `BrainItem` with `source_status: str | None`.
- Extends `save_item(content: str, title: str, summary: str, item_type: str, tags: list[str], source_type: str, capture_mode: str, *, capture_key: str | None = None, source_url: str | None = None, source_published_at: datetime | None = None, source_status: str | None = None, due_at: datetime | None = None, timezone_name: str | None = None, recurrence_rule: str | None = None) -> BrainItem`.
- Extends `CaptureItem` with `source_status: str = "bookmark"`.

- [ ] **Step 1: Write failing migration and unavailable-link tests**

```python
def test_initialize_schema_adds_legacy_source_status_without_changing_existing_url(self):
    with sqlite3.connect(config.DATABASE_PATH) as connection:
        connection.execute(
            "CREATE TABLE brain_items (id INTEGER PRIMARY KEY, content TEXT NOT NULL, title TEXT NOT NULL, "
            "summary TEXT NOT NULL, item_type TEXT NOT NULL, tags_json TEXT NOT NULL, source_type TEXT NOT NULL, "
            "source_url TEXT, source_published_at TEXT, capture_mode TEXT NOT NULL, capture_key TEXT UNIQUE, "
            "status TEXT NOT NULL, due_at TEXT, timezone TEXT, recurrence_rule TEXT, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, last_recalled_at TEXT, last_organized_at TEXT, completed_at TEXT)"
        )
        connection.execute(
            "INSERT INTO brain_items (content, title, summary, item_type, tags_json, source_type, source_url, "
            "capture_mode, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("Read this", "Article", "Saved link", "reference", "[]", "capture", "https://example.com/article", "explicit", "active", "2026-07-28T00:00:00+00:00", "2026-07-28T00:00:00+00:00"),
        )
    brain_store.initialize_schema()
    item = brain_store.get_item(1)
    self.assertEqual((item.source_url, item.source_status), ("https://example.com/article", "legacy"))

async def test_capture_keeps_an_unavailable_public_url_as_a_reference_item(self):
    source = CapturedSource("https://example.com/unreadable", None, None, None, "manual_description")
    capture = await analyze_and_save_capture(build_capture_plan("save this", [source]))
    item = brain_store.list_capture_items(capture.id)[0]
    self.assertEqual((item.source_url, item.source_status), ("https://example.com/unreadable", "unavailable"))
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m unittest tests.test_brain_store.BrainStoreTests.test_initialize_schema_adds_legacy_source_status_without_changing_existing_url tests.test_brain_analysis.BrainAnalysisTests.test_capture_keeps_an_unavailable_public_url_as_a_reference_item -v`

Expected: FAIL because `source_status` does not exist and unreadable sources are skipped.

- [ ] **Step 3: Add the idempotent schema change and data mapping**

In `_create_brain_tables`, add `source_status TEXT` after `source_published_at`. In `_add_missing_brain_item_columns`, add the same column when it is absent, then run `UPDATE brain_items SET source_status = 'legacy' WHERE source_status IS NULL AND source_url IS NOT NULL AND TRIM(source_url) != ''`. Add `source_status` to `BrainItem`, `brain_item_from_row`, `save_item`, and the `save_capture` insert statement. Set `source_status` to `"bookmark"` when a new `save_item` has a non-empty `source_url` but receives no explicit status. Validate non-`None` statuses with:

```python
SOURCE_STATUSES = {"analyzed", "bookmark", "unavailable", "legacy"}

if source_status is not None and source_status not in SOURCE_STATUSES:
    raise ValueError("Unsupported source status.")
```

- [ ] **Step 4: Keep every supplied capture URL**

Replace the planner’s `if source.status == "manual_description": continue` branch. Create a `CaptureItem` for every source. For `manual_description`, use `content=clean_request`, `title=source.url`, `summary="Saved link that Roxy could not read."`, `source_url=source.url`, and `source_status="unavailable"`. For `bookmark`, use `source_status="bookmark"`; for analyzed sources, use `source_status="analyzed"` and retain source text as `content`.

- [ ] **Step 5: Run source persistence tests**

Run: `python -m unittest tests.test_brain_store tests.test_brain_analysis -v`

Expected: PASS, including old-row migration, readable link capture, unavailable-link capture, and invalid-status rejection.

### Task 2: Implement lightweight shared retrieval without new tables

**Files:**
- Create: `src/knowledge/retrieval.py`
- Modify: `src/knowledge/brain_store.py`
- Test: `tests/test_retrieval.py`

**Interfaces:**
- Produces `SearchResult(item: BrainItem, match_fields: list[str], score: int)`.
- Produces `search_brain(query: str, *, limit: int = 20, item_type: str | None = None) -> list[SearchResult]`.
- Keeps `search_items(query: str, limit: int = 20, item_type: str | None = None) -> list[BrainItem]` as a compatibility wrapper.

- [ ] **Step 1: Write failing retrieval tests**

```python
def test_search_brain_finds_thought_text_tag_and_url(self):
    thought = brain_store.save_item("Learn Hermes agents", "Learn Hermes", "Learning goal", "goal", ["domain:ai"], "text", "explicit")
    link = brain_store.save_item("Medium Hermes article", "Hermes article", "Saved link", "reference", [], "capture", "explicit", source_url="https://medium.com/hermes")

    self.assertEqual(retrieval.search_brain("Hermes")[0].item.id, thought.id)
    self.assertEqual(retrieval.search_brain("ai")[0].item.id, thought.id)
    self.assertEqual(retrieval.search_brain("medium links")[0].item.id, link.id)

def test_search_brain_escapes_punctuation_and_excludes_archived_items(self):
    item = brain_store.save_item("C++ notes", "C++", "Notes", "idea", [], "text", "explicit")
    self.assertEqual(retrieval.search_brain('C++ "notes"')[0].item.id, item.id)
    brain_store.archive_item(item.id)
    self.assertEqual(retrieval.search_brain("C++"), [])
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m unittest tests.test_retrieval -v`

Expected: FAIL because the shared retrieval module does not exist.

- [ ] **Step 3: Implement literal query normalization**

Implement `normalize_query(query: str) -> list[str]` in `src/knowledge/retrieval.py`. Lowercase and split whitespace, discard only `give`, `me`, `my`, `show`, `saved`, `please`, `link`, and `links`, and retain all other non-empty tokens. Implement `fts_query(tokens)` by replacing every `"` inside a token with `""` and wrapping each token in double quotes. Return `[]` for no retained tokens.

- [ ] **Step 4: Merge the existing FTS results with simple URL and tag matches**

Use the existing `brain_items_fts` query for thought matches. Add one parameterized query against `brain_items` for each token:

```sql
SELECT * FROM brain_items
WHERE status = 'active'
  AND (? IS NULL OR item_type = ?)
  AND (LOWER(COALESCE(source_url, '')) LIKE ? OR LOWER(tags_json) LIKE ?)
ORDER BY updated_at DESC, id DESC
LIMIT ?
```

Pass `f"%{token.lower()}%"` for both `LIKE` parameters. Merge rows by ID, assign score `2` for FTS, add `3` for URL match, add `3` for tag match, then sort by `score DESC, item.updated_at DESC, item.id DESC`. Return `match_fields` from `content`, `url`, and `tag` according to the matches. Query at `max(limit * 3, 30)` and apply `limit` after merging.

- [ ] **Step 5: Preserve current callers**

Replace `brain_store.search_items` with:

```python
def search_items(query: str, limit: int = 20, item_type: str | None = None) -> list[BrainItem]:
    from src.knowledge.retrieval import search_brain
    return [result.item for result in search_brain(query, limit=limit, item_type=item_type)]
```

- [ ] **Step 6: Run retrieval tests**

Run: `python -m unittest tests.test_retrieval tests.test_brain_store -v`

Expected: PASS, covering thought text, tags, URLs, punctuation, archived exclusion, and existing callers.

### Task 3: Restrict recall to the explicit tool and simplify model thinking

**Files:**
- Modify: `src/knowledge/tools.py`
- Modify: `src/handlers/chat.py`
- Modify: `src/prompts/system.py`
- Modify: `src/knowledge/brain_analysis.py`
- Test: `tests/test_brain_store.py`
- Test: `tests/test_chat.py`
- Test: `tests/test_brain_analysis.py`

**Interfaces:**
- `search_saved_items` consumes `retrieval.search_brain` and returns each item plus `match_fields` and `source_status`.
- `mark_items_recalled(item_ids: list[int]) -> None` updates `last_recalled_at` after a successful non-empty tool response.

- [ ] **Step 1: Write failing tool and chat-boundary tests**

```python
def test_search_tool_returns_source_status_and_match_fields(self):
    item = brain_store.save_item("Watch this", "Video", "Saved link", "reference", [], "capture", "explicit", source_url="https://youtube.com/shorts/abc", source_status="bookmark")
    result = asyncio.run(tools.search_saved_items('{"query":"youtube", "item_type":null}'))
    self.assertEqual(result["brain_items"][0]["source_status"], "bookmark")
    self.assertEqual(result["brain_items"][0]["match_fields"], ["url"])

def test_build_messages_do_not_search_saved_items_implicitly(self):
    with patch("src.handlers.chat.brain_store.search_items") as search:
        chat.build_burst_messages([PendingMessage(1, "hello", AsyncMock())])
        chat.build_photo_message([], "https://example.com/image.jpg", "hello")
    search.assert_not_called()
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m unittest tests.test_brain_store tests.test_chat -v`

Expected: FAIL because tool results lack shared match metadata and both chat builders call `build_brain_context`.

- [ ] **Step 3: Return shared retrieval results and mark explicit recall**

Make `search_saved_items` call `retrieval.search_brain`. Return existing item fields plus `source_status` and `match_fields`; retain the existing `source_url` field so callers remain compatible. After creating a non-empty successful response, call `brain_store.mark_items_recalled([result.item.id for result in results])`. Do not update timestamps for empty, invalid, or failed searches.

- [ ] **Step 4: Remove implicit Brain database access from chat**

Remove `build_brain_context` calls and their system-message insertion from `build_burst_messages` and `build_photo_message`. Keep the function only if another caller needs it; otherwise delete it and its tests. Update the system prompt with: “When the user asks about previously saved information, call `search_saved_items` using meaningful topic, tag, URL, host, or path terms. Answer only from returned records. If no result is returned, say you could not find it.”

- [ ] **Step 5: Keep enrichment and relation thinking bounded**

Continue storing original `content` unchanged. Reject analysis values with empty title/summary, unsupported item type, or more than 12 tags; fall back to caller-provided metadata when analysis fails. Keep relation explanations concise and user-visible; do not add a second retrieval system or save hidden model reasoning.

- [ ] **Step 6: Run recall and analysis tests**

Run: `python -m unittest tests.test_brain_analysis tests.test_brain_store tests.test_chat -v`

Expected: PASS, including tool-only recall, source status, recall timestamp behavior, and no hidden-reasoning persistence.

### Task 4: Reuse simple retrieval in the dashboard and page active items with offsets

**Files:**
- Modify: `src/dashboard/service.py`
- Modify: `src/web.py`
- Modify: `src/templates/brain.html`
- Modify: `src/knowledge/brain_store.py`
- Test: `tests/test_dashboard.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces `list_active_items_page(offset: int, limit: int) -> list[BrainItem]`.
- Produces `search_brain_snapshot(query: str) -> list[dict[str, object]]` from `retrieval.search_brain`.
- Exposes authenticated `GET /brain/search?q=<query>` and `GET /brain-data?offset=<offset>&limit=<limit>`.

- [ ] **Step 1: Write failing dashboard and endpoint tests**

```python
def test_active_item_page_returns_the_next_records(self):
    for index in range(101):
        brain_store.save_item(f"Note {index}", f"Note {index}", "Saved", "idea", [], "text", "explicit")
    self.assertEqual(len(brain_store.list_active_items_page(0, 100)), 100)
    self.assertEqual(len(brain_store.list_active_items_page(100, 100)), 1)

def test_authenticated_brain_search_returns_shared_url_match(self):
    response = self.client.get("/brain/search", params={"q": "medium"})
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["items"][0]["match_fields"], ["url"])
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m unittest tests.test_dashboard tests.test_web -v`

Expected: FAIL because paging and the retrieval-backed search endpoint do not exist.

- [ ] **Step 3: Add simple offset paging and one dashboard serializer**

Implement `list_active_items_page` with `LIMIT ? OFFSET ?`, ordered by `created_at DESC, id DESC`. Reject negative offsets and limits outside `1..100`. Extract `_brain_item_record(item, context, match_fields=[])` in `src/dashboard/service.py`; include current fields plus `source_status` and `match_fields`. Use it for both the initial/page listing and search results.

- [ ] **Step 4: Add the authenticated search route**

In `src/web.py`, read `q` from `request.query_params`, return HTTP 400 for blank or filler-only input, and return `{"items": dashboard.search_brain_snapshot(query)}` on success. Use the existing `run_brain_action` wrapper and return the established authenticated 503 response when SQLite is unavailable. Do not parse a GET body with `parse_qs`.

- [ ] **Step 5: Keep the browser UI minimal**

Add a debounced search field. For non-empty input, request `/brain/search?q=<encoded query>` and render the matching records. For empty input, request the first `/brain-data` page. Add a “Load more” control that requests the next offset while it returns a full page. Render `source_url` and a short source-status label; do not display extracted content separately because it is already the item content.

- [ ] **Step 6: Run dashboard tests**

Run: `python -m unittest tests.test_dashboard tests.test_web -v`

Expected: PASS, including authentication, URL search, archived exclusion, source status, offsets, invalid query parameters, and database failures.

### Task 5: Document the lean model and run the full suite

**Files:**
- Modify: `README.md`
- Modify: `tests/test_documentation.py`
- Test: `tests/test_brain_store.py`
- Test: `tests/test_retrieval.py`
- Test: `tests/test_brain_analysis.py`
- Test: `tests/test_chat.py`
- Test: `tests/test_dashboard.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Update README Brain documentation**

Document that Roxy uses one Brain record per thought or saved link; readable links store useful page text, while unavailable links remain as saved bookmarks. Include “what did I save about Hermes agent?” and “give me my Medium links” as recall examples. State that `/brain` can search and browse saved active records.

- [ ] **Step 2: Add a documentation test**

```python
def test_readme_describes_simple_link_capture_and_recall(self):
    readme = Path("README.md").read_text()
    self.assertIn("one Brain record per thought or saved link", readme)
    self.assertIn("give me my Medium links", readme)
    self.assertIn("/brain", readme)
```

- [ ] **Step 3: Run the full suite**

Run: `python -m unittest discover -s tests -v`

Expected: PASS with migration, readable/unavailable capture, shared retrieval, tool-only recall, and dashboard paging coverage.

- [ ] **Step 4: Commit**

Do not commit. Repository instructions require explicit user authorization before creating a commit.

## Plan Self-Review

- **Schema simplicity:** The implementation adds one nullable status column and no new Brain tables.
- **Data preservation:** Existing data remains intact and existing URL rows receive `legacy` status automatically.
- **Capture reliability:** Every explicit URL produces an item; no second table or follow-up state machine is required.
- **Search coverage:** FTS searches thought/page content, while parameterized `LIKE` searches existing URL and tag fields. This is appropriate for a small personal SQLite database.
- **Thinking safety:** Saved-memory recall is explicit and evidence-bound; enrichment and relation metadata never contain hidden reasoning.
- **Dashboard scope:** Offset paging is intentionally simpler than cursor pagination and sufficient for a single-user local dashboard.
