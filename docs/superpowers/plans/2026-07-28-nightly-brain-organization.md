# Nightly Brain Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organize active Brain records from the last 10 days at 3 AM by refreshing their metadata and relationships while preserving original content exactly.

**Architecture:** Extend the Brain-item schema and repository with an organization timestamp, a bounded selector, and a metadata-only update operation. Extend the existing introspection service to analyze the selected records using the established structured-analysis contract, persist valid metadata, and then refresh relations; the existing reminder worker remains the sole scheduler.

**Tech Stack:** Python 3, SQLite/FTS5, `unittest`, `unittest.mock`, existing OpenAI structured chat completion client.

## Global Constraints

- Run through the existing reminder worker at 3:00 AM in `TASK_TIMEZONE`; do not add cron, another worker, or a scheduler dependency.
- Only active Brain items created within the previous 10 days are eligible.
- Preserve `content`, source/capture metadata, status, reminder scheduling, and completion fields exactly.
- Update only `title`, `summary`, `tags_json`, `updated_at`, and `last_organized_at`; update `item_type` only for non-task records.
- Keep organization incremental and bounded; use a maximum batch size of 20 records per nightly run.
- Prioritize never-organized records, then records with weak metadata, then the oldest `last_organized_at`.
- Use `src.core.errors.try_async` for asynchronous failure recovery; do not add direct `try`/`except` outside `src/core/errors.py`.
- Invalid or unavailable analysis leaves a record unchanged and eligible for a future run.
- Do not commit unless the user explicitly requests a commit.

---

## File structure

- `src/knowledge/brain_store.py`: schema compatibility migration, `BrainItem.last_organized_at`, ordered recent-item selection, and metadata-only persistence.
- `src/knowledge/introspection.py`: ten-day organization selection and per-record metadata/relationship refresh.
- `tests/test_brain_store.py`: persistence, migration, ordering, and field-preservation coverage.
- `tests/test_introspection.py`: nightly organization selection, successful refresh, error recovery, and relation-refresh coverage.
- `tests/test_worker.py`: retain and update the worker integration assertion if the introspection service return value changes.

### Task 1: Add organization storage and safe repository operations

**Files:**
- Modify: `src/knowledge/brain_store.py`
- Modify: `tests/test_brain_store.py`

**Interfaces:**
- Produces: `BrainItem.last_organized_at: datetime | None`.
- Produces: `list_recent_items_for_organization(now: datetime, limit: int = 20) -> list[BrainItem]`.
- Produces: `update_organized_metadata(item_id: int, analysis: BrainAnalysis, organized_at: datetime) -> BrainItem | None` (import `BrainAnalysis` only under `TYPE_CHECKING` to avoid a runtime circular dependency).

- [ ] **Step 1: Write failing repository tests**

Add to `tests/test_brain_store.py`:

```python
def test_organization_selection_includes_only_recent_active_items_in_priority_order(self):
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    never_organized = brain_store.save_item("Never content", "Never", "Clear summary", "idea", [], "text", "explicit")
    weak = brain_store.save_item("Weak", "Weak", "", "idea", [], "text", "explicit")
    organized = brain_store.save_item("Organized", "Organized", "Clear summary", "idea", [], "text", "explicit")
    old = brain_store.save_item("Old", "Old", "", "idea", [], "text", "explicit")
    with brain_store._brain_database() as connection:
        connection.execute("UPDATE brain_items SET last_organized_at = ? WHERE id = ?", ((now - timedelta(days=2)).isoformat(), organized.id))
        connection.execute("UPDATE brain_items SET created_at = ? WHERE id = ?", ((now - timedelta(days=11)).isoformat(), old.id))

    selected = brain_store.list_recent_items_for_organization(now, limit=20)

    self.assertEqual([item.id for item in selected], [never_organized.id, weak.id, organized.id])

def test_update_organized_metadata_preserves_content_and_task_fields(self):
    due_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    item = brain_store.save_item("Keep this exact content", "Old", "Old", "task", [], "text", "explicit", due_at=due_at, timezone_name="Asia/Kolkata", recurrence_rule="daily")
    analysis = BrainAnalysis("Clean title", "Clean factual summary.", "goal", ["entity:roxy", "domain:planning"])

    updated = brain_store.update_organized_metadata(item.id, analysis, datetime(2026, 7, 28, tzinfo=timezone.utc))

    self.assertEqual(updated.content, "Keep this exact content")
    self.assertEqual((updated.title, updated.summary, updated.item_type, updated.tags), ("Clean title", "Clean factual summary.", "task", ["entity:roxy", "domain:planning"]))
    self.assertEqual((updated.due_at, updated.timezone, updated.recurrence_rule), (due_at, "Asia/Kolkata", "daily"))
    self.assertEqual(updated.last_organized_at, datetime(2026, 7, 28, tzinfo=timezone.utc))

    idea = brain_store.save_item("Idea", "Idea", "Idea", "idea", [], "text", "explicit")
    retyped = brain_store.update_organized_metadata(idea.id, analysis, datetime(2026, 7, 28, tzinfo=timezone.utc))
    self.assertEqual(retyped.item_type, "goal")
```

Import `datetime`, `timedelta`, and `timezone` from `datetime`, plus `BrainAnalysis` from `src.knowledge.brain_analysis`.

- [ ] **Step 2: Run the repository tests to verify failure**

Run: `python -m unittest tests.test_brain_store -v`

Expected: FAIL because `last_organized_at`, `list_recent_items_for_organization`, and `update_organized_metadata` do not exist.

- [ ] **Step 3: Add schema migration, model field, selector, and update operation**

In `src/knowledge/brain_store.py`, add `last_organized_at TEXT` to the `brain_items` create-table statement, after `last_recalled_at`. Add a compatibility helper called from `_initialize_schema`:

```python
def _add_missing_brain_item_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(brain_items)")
    }
    if "last_organized_at" not in columns:
        connection.execute("ALTER TABLE brain_items ADD COLUMN last_organized_at TEXT")
```

Call it immediately after `_create_brain_tables(connection)`. Add the dataclass field and row mapping:

```python
last_organized_at: datetime | None

last_organized_at=(
    parse_timestamp(row["last_organized_at"])
    if row["last_organized_at"] else None
),
```

Add the selector and updater after `list_active_items`:

```python
def list_recent_items_for_organization(
    now: datetime, limit: int = 20
) -> list[BrainItem]:
    cutoff = format_timestamp(now.astimezone(timezone.utc) - timedelta(days=10))
    with _brain_database() as connection:
        rows = connection.execute(
            "SELECT * FROM brain_items WHERE status = 'active' AND created_at >= ? "
            "ORDER BY CASE "
            "WHEN last_organized_at IS NULL AND summary <> '' AND summary <> title AND title <> content THEN 0 "
            "WHEN last_organized_at IS NULL THEN 1 "
            "WHEN summary = '' OR summary = title OR title = content THEN 2 ELSE 3 END, "
            "last_organized_at ASC, created_at ASC, id ASC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [brain_item_from_row(row) for row in rows]

def update_organized_metadata(
    item_id: int, analysis: "BrainAnalysis", organized_at: datetime
) -> BrainItem | None:
    with _brain_database() as connection:
        connection.execute(
            "UPDATE brain_items SET title = ?, summary = ?, item_type = CASE "
            "WHEN item_type = 'task' THEN item_type ELSE ? END, tags_json = ?, "
            "updated_at = ?, last_organized_at = ? WHERE id = ? AND status = 'active'",
            (
                analysis.title, analysis.summary, analysis.item_type,
                json.dumps(analysis.tags), format_timestamp(organized_at),
                format_timestamp(organized_at), item_id,
            ),
        )
        row = connection.execute("SELECT * FROM brain_items WHERE id = ?", (item_id,)).fetchone()
    return brain_item_from_row(row) if row else None
```

Import `timedelta` and `TYPE_CHECKING`; add:

```python
if TYPE_CHECKING:
    from src.knowledge.brain_analysis import BrainAnalysis
```

- [ ] **Step 4: Run focused repository tests**

Run: `python -m unittest tests.test_brain_store -v`

Expected: PASS, including the two new organization tests.

### Task 2: Organize recent records during nightly introspection

**Files:**
- Modify: `src/knowledge/introspection.py`
- Modify: `tests/test_introspection.py`

**Interfaces:**
- Consumes: `brain_store.list_recent_items_for_organization(now, limit=20)` and `brain_store.update_organized_metadata(item_id, analysis, organized_at)` from Task 1.
- Consumes: `ask_brain_analysis(content: str, item_type: str) -> BrainAnalysis | None` and `relation_candidates(item: BrainItem) -> list[RelationCandidate]`.
- Produces: `eligible_for_introspection(now: datetime) -> list[BrainItem]` selecting at most 20 active items from the last 10 days.
- Produces: `refresh_brain_connections(now: datetime) -> int` returning the count of successfully organized records.

- [ ] **Step 1: Replace the current selection test with 10-day bounded coverage**

In `tests/test_introspection.py`, update the two existing nightly-refresh tests so they do not call the live model:

```python
async def test_nightly_refresh_never_creates_a_synthetic_item(self):
    brain_store.save_item("Recent thought", "Recent", "Recent", "idea", ["domain:work"], "text", "explicit")
    before = len(brain_store.list_recent_items(100))
    with patch("src.knowledge.introspection.ask_brain_analysis", new=AsyncMock(return_value=None)):
        await refresh_brain_connections(datetime.now(timezone.utc))
    self.assertEqual(len(brain_store.list_recent_items(100)), before)

async def test_automatic_save_skips_relation_analysis_until_nightly_refresh(self):
    from src.knowledge import tools

    arguments = ('{"content":"Lakshay is an AI engineer","title":"Lakshay",'
                 '"summary":"Lakshay works in AI","item_type":"fact",'
                 '"tags":["domain:career"],"capture_mode":"automatic"}')
    analysis = BrainAnalysis("Lakshay", "Lakshay works in AI.", "fact", ["domain:career"])
    with patch("src.knowledge.introspection.ask_brain_analysis", new=AsyncMock(return_value=analysis)), patch(
        "src.knowledge.introspection.relation_candidates", new=AsyncMock(return_value=[])
    ) as relation_candidates:
        result = await tools.save_brain_item(arguments, capture_key="telegram:7:12:0")
        relation_candidates.assert_not_awaited()
        await refresh_brain_connections(datetime.now(timezone.utc))

    self.assertTrue(result["ok"])
    relation_candidates.assert_awaited_once()
```

Replace `test_eligible_items_include_recent_and_unconnected_records` with:

```python
async def test_eligible_items_include_only_active_records_from_the_last_ten_days(self):
    recent = brain_store.save_item("Recent", "Recent", "Recent", "idea", [], "text", "explicit")
    old = brain_store.save_item("Old", "Old", "Old", "idea", [], "text", "explicit")
    archived = brain_store.save_item("Archived", "Archived", "Archived", "idea", [], "text", "explicit")
    now = datetime.now(timezone.utc)
    with brain_store._brain_database() as connection:
        connection.execute("UPDATE brain_items SET created_at = ? WHERE id = ?", ((now - timedelta(days=11)).isoformat(), old.id))
        connection.execute("UPDATE brain_items SET status = 'archived' WHERE id = ?", (archived.id,))

    eligible = eligible_for_introspection(now)

    self.assertEqual([item.id for item in eligible], [recent.id])
```

Add a successful organization test:

```python
async def test_nightly_refresh_rewrites_metadata_but_preserves_content_and_refreshes_relations(self):
    source = brain_store.save_item("Ruchi works at Acme", "Old title", "Old", "idea", [], "text", "explicit")
    target = brain_store.save_item("Acme project", "Acme", "Project", "project", ["entity:acme"], "text", "explicit")
    with brain_store._brain_database() as connection:
        connection.execute("UPDATE brain_items SET created_at = ? WHERE id = ?", ((datetime.now(timezone.utc) - timedelta(days=11)).isoformat(), target.id))
    analysis = BrainAnalysis("Ruchi at Acme", "Ruchi works at Acme.", "fact", ["entity:acme"])
    relation = RelationCandidate(target.id, "same entity", "Both records refer to Acme.", 1.0, "direct")
    with patch("src.knowledge.introspection.ask_brain_analysis", new=AsyncMock(return_value=analysis)), patch(
        "src.knowledge.introspection.relation_candidates", new=AsyncMock(return_value=[relation])
    ):
        refreshed = await refresh_brain_connections(datetime.now(timezone.utc))

    updated = brain_store.get_item(source.id)
    self.assertEqual(refreshed, 1)
    self.assertEqual(updated.content, "Ruchi works at Acme")
    self.assertEqual((updated.title, updated.summary, updated.tags), ("Ruchi at Acme", "Ruchi works at Acme.", ["entity:acme"]))
    self.assertIsNotNone(updated.last_organized_at)
    self.assertEqual(brain_store.list_item_relations(source.id)[0]["target_item_id"], target.id)
```

Add an error-recovery test:

```python
async def test_nightly_refresh_leaves_record_unchanged_when_analysis_is_unavailable(self):
    item = brain_store.save_item("Exact", "Old title", "Old summary", "idea", [], "text", "explicit")
    with patch("src.knowledge.introspection.ask_brain_analysis", new=AsyncMock(return_value=None)):
        refreshed = await refresh_brain_connections(datetime.now(timezone.utc))

    unchanged = brain_store.get_item(item.id)
    self.assertEqual(refreshed, 0)
    self.assertEqual((unchanged.content, unchanged.title, unchanged.summary, unchanged.last_organized_at), ("Exact", "Old title", "Old summary", None))
```

Import `BrainAnalysis` and `RelationCandidate` from `src.knowledge.brain_analysis`.

- [ ] **Step 2: Run the introspection tests to verify failure**

Run: `python -m unittest tests.test_introspection -v`

Expected: FAIL because the old job does not call metadata analysis or update `last_organized_at`, and it still includes old unconnected records.

- [ ] **Step 3: Replace connection-only refresh with organization plus relation refresh**

In `src/knowledge/introspection.py`, set:

```python
RECENT_ITEM_DAYS = 10
MAX_ELIGIBLE_ITEMS = 20
```

Replace `eligible_for_introspection` and `refresh_brain_connections` with:

```python
def eligible_for_introspection(now: datetime) -> list[brain_store.BrainItem]:
    return brain_store.list_recent_items_for_organization(now, MAX_ELIGIBLE_ITEMS)

async def refresh_brain_connections(now: datetime) -> int:
    refreshed = 0
    for item in eligible_for_introspection(now):
        analysis = await ask_brain_analysis(item.content, item.item_type)
        if analysis is None:
            continue
        organized = brain_store.update_organized_metadata(item.id, analysis, now)
        if organized is None:
            continue
        candidates = await relation_candidates(organized)
        if candidates:
            save_relation_candidates(organized.id, candidates)
        refreshed += 1
    return refreshed
```

Update imports to include `ask_brain_analysis`; remove unused `timedelta` and `BATCH_SIZE`. Keep `next_introspection_at` unchanged.

- [ ] **Step 4: Run focused introspection tests**

Run: `python -m unittest tests.test_introspection -v`

Expected: PASS, including ten-day eligibility, content preservation, failed-analysis preservation, relation refresh, and the existing 3 AM assertion.

### Task 3: Verify worker integration and full regression suite

**Files:**
- Modify: `tests/test_worker.py` only if Task 2 changes the function’s observable worker contract.

**Interfaces:**
- Consumes: `ReminderWorker.run_introspection_if_due(now: datetime | None = None) -> bool`.
- Verifies: the worker invokes `refresh_brain_connections` once after 3 AM and does not run it twice on the same local date.

- [ ] **Step 1: Make the scheduler test timezone-explicit**

In `tests/test_worker.py`, change the test timestamp to an actual 3:00 AM `Asia/Kolkata` instant:

```python
at_three_am = datetime(2026, 7, 27, 21, 30, tzinfo=timezone.utc)
```

Keep the assertions that the first call returns `True`, the second returns `False`, and `refresh_brain_connections` is awaited once with `at_three_am`.

- [ ] **Step 2: Run worker tests**

Run: `python -m unittest tests.test_worker -v`

Expected: PASS; reminder processing continues to be covered independently from the background organization hook.

- [ ] **Step 3: Run the complete test suite and static checks**

Run: `python -m unittest discover -s tests -v && git diff --check`

Expected: all tests PASS and no whitespace errors in files changed for this feature.

- [ ] **Step 4: Review the final diff without committing**

Run: `git diff -- src/knowledge/brain_store.py src/knowledge/introspection.py tests/test_brain_store.py tests/test_introspection.py tests/test_worker.py docs/superpowers/specs/2026-07-28-nightly-brain-organization-design.md`

Expected: only the approved nightly-organization behavior, its tests, and its design documentation are present. Do not create a commit unless the user explicitly asks.
