# Roxy Minimal Second-Brain Implementation Plan

## Goal

Turn Roxy's existing Brain into a simple and reliable second brain that:

- saves notes, links, photos, voice text, and tasks quickly;
- preserves the original user content;
- searches saved information with SQLite FTS5;
- retrieves memory only when the user clearly asks for it;
- keeps task scheduling, the dashboard, export, and deletion behaviour simple.

The design must stay in one Python codebase with one SQLite database. The
existing reminder-delivery process remains separate from Brain URL enrichment.

---

## Current Architecture

Roxy currently stores Brain records in `brain_items`, with separate settings, capture-audit, relation, and organization-lock tables. Those tables support features that are not needed for a minimal second brain and make the data model harder to understand. `brain_items` remains the main user-facing and task-compatible record.

```text
                         CURRENT ARCHITECTURE

                 ┌──────────────────────────┐
                 │     SQLite: roxy.db      │
                 └────────────┬─────────────┘
                              │
          ┌───────────────────┼────────────────────┐
          │                   │                    │
          v                   v                    v
 ┌─────────────────┐ ┌─────────────────┐ ┌──────────────────┐
 │ brain_items     │ │ brain_items_fts │ │ extra Brain      │
 │ notes, links,   │ │                 │ │ tables to remove │
 │ tasks, metadata │ │                 │ │                  │
 └─────────────────┘ └─────────────────┘ └──────────────────┘
```

### Current problems

1. Link reading may happen before a record is saved, so saving can feel slow.
2. Original user content and AI-generated metadata are stored too closely together.
3. Unreadable links may not be saved.
4. Normal chat automatically searches Brain memory even when memory is not needed.
5. Capture audit trails, relation graphs, organization locks, and database-backed auto-capture settings add complexity without improving the core save-and-recall workflow.

---

## Design Principles

- Keep `brain_items` as the main user-facing and task-compatible record.
- Do not add any new Brain table.
- Save first, enrich later.
- Keep FTS5 as the default search system.
- Do not add PostgreSQL, a vector database, Redis, Celery, a broker, microservices, or a new worker process for Brain enrichment.
- Do not retain Brain capture audits, relation graphs, organization locks, database-backed auto-capture settings, or embeddings.
- Do not store internal prompts, hidden reasoning, chain-of-thought, or model scratch work.
- Archived and deleted items must never appear in active search results.

---

## Target Architecture

```text
                    Telegram / Dashboard
                            │
                            v
                    ┌─────────────────┐
                    │ Chat / Web Flow │
                    └────────┬────────┘
                             │
                ┌────────────┴────────────┐
                │                         │
                v                         v
       ┌─────────────────┐       ┌─────────────────┐
       │ Brain Service   │       │ Assistant / LLM │
       │ save/search/etc │       │ tools + reply   │
       └────────┬────────┘       └─────────────────┘
                │
                v
       ┌─────────────────────────────────┐
       │          SQLite: roxy.db        │
       │                                 │
       │ brain_items                     │
       │ brain_items_fts                 │
       │ scheduled_deliveries             │
       └──────────────┬──────────────────┘
                      │
                      v
             ┌──────────────────┐
             │ In-app asyncio   │
             │ runner fetches   │
             │ pending URLs     │
             └──────────────────┘
```

Existing `reminder_worker.py` continues to deliver scheduled tasks. It no
longer runs Brain organization or URL enrichment.

---

## Minimal Data Model

### 1. Keep `brain_items`

`brain_items` remains the main record used by the dashboard, tasks, scheduled deliveries, archive, delete, and tools.

It continues to hold the user-facing record and task fields:

- title;
- content;
- summary;
- tags;
- type;
- URL;
- lifecycle status;
- task scheduling fields.

Do not redesign or replace this table during this implementation.

Original text remains in `content` and is never changed by enrichment; the
submitted URL remains in `source_url`. Add one nullable `source_status`
column: `pending`, `ready`, `unavailable`, or `legacy` for URL items, and
`NULL` for non-URL items. Do not add a source-evidence table, join table, URL
snapshot table, revision history, or enriched-content column.

For a URL item, the background runner may fill an empty `title` or `summary`,
or replace an automatic placeholder with fetched metadata. It must never
replace user-provided `title`, `summary`, `content`, or `source_url`.

### 2. Keep `brain_items_fts`

Continue using the existing FTS5 index over the searchable item fields.

FTS5 remains the default retrieval system for the MVP.

### 3. Keep `scheduled_deliveries`

Rename `reminder_deliveries` to `scheduled_deliveries`. It is not a Brain feature: it is the small, separate task-scheduling ledger needed for recurring occurrences, delivery leases, retries, and errors.

```sql
ALTER TABLE reminder_deliveries RENAME TO scheduled_deliveries;
```

Keep its current columns and foreign key to `brain_items`. SQLite cannot rename
an index, so drop `reminder_deliveries_due_index` after the table rename and
create `scheduled_deliveries_due_index` on the renamed table.

### Removed Brain tables and features

First remove all schema-initialization code and application reads that recreate
or depend on these tables. Then remove the tables and their dependent UI,
tools, exports, and tests:

- `brain_settings`: remove the database-backed auto-capture toggle; capture behaviour is determined directly by the application flow.
- `brain_captures` and `brain_capture_items`: do not retain AI analysis audit records or capture-to-item join records.
- `brain_item_relations`: remove generated relations, the relationship ledger, and the knowledge-map UI.
- `brain_organization_lock`: remove the scheduled/manual Brain organization process that requires it.

The durable user data remains in `brain_items`. Export the removed-table data
before dropping it so existing data can be recovered if needed.

---

## Capture Flow

### Text, photo caption, voice transcript, or file text

```text
User saves content
       │
       v
Create brain_items row
       │
       v
Commit one SQLite transaction
       │
       ├── Reply to user: saved
       │
       v
Saved item is available immediately
```

Once the application has decided to capture the request, the user must receive
a success acknowledgement immediately after the SQLite transaction succeeds.
The agent's initial tool-selection call can still take time; URL fetching and
metadata enrichment must never delay the save.

Any later URL-fetch failure must not delete the item or prevent FTS search.

### Public URL

```text
User sends URL
       │
       v
Save URL immediately
       │
       ├── brain_items
       └── brain_items(source_status = pending)
       │
       v
Reply to user: saved
       │
       v
Background URL fetch
       │
       ├── success
       │     ├── fill missing or placeholder title/summary when useful
       │     └── set source_status = ready
       │
       └── failure
             ├── keep URL as bookmark
             └── set source_status = unavailable
```

An unreadable URL must remain visible as a saved bookmark. Roxy may ask the user for a description, but the URL must not disappear.

---

## Background Processing

Use one application-owned `asyncio.Queue` and one background coroutine.

```text
Application startup
       │
       ├── start one background runner
       └── enqueue unfinished items from SQLite

New saved item
       │
       └── enqueue brain_item_id

Background runner
       ├── fetch URL when needed
       └── update only missing or placeholder title/summary
```

Do not add:

- an external queue;
- Redis;
- Celery;
- multiple URL-enrichment worker processes;
- a job table;
- worker leases;
- complex retry scheduling.

Use simple retry behaviour: a runner failure sets `source_status` to
`unavailable` and keeps the bookmark usable. Only `pending` items are retried
at application startup. A later dashboard action may explicitly reset an
`unavailable` item to `pending` and enqueue it again.

Startup recovery can query pending URL sources:

```sql
SELECT id
FROM brain_items
WHERE source_url IS NOT NULL AND source_status = 'pending';
```

URL records with `source_status = 'pending'` should also be re-enqueued.

---

## Retrieval Flow

Memory retrieval should run only when the user clearly asks Roxy to remember or find saved information.

Examples:

- "What did I save about SQLite?"
- "Find my note about the expense tracker."
- "What did we decide about the database?"
- "Do you remember my RDBMS learning plan?"

Normal conversation, unrelated text, and photo captions must not automatically receive Brain context.

```text
Clear recall request
       │
       v
Search active brain_items
       │
       ├── exact normalized title, tag, or URL match
       └── FTS5 keyword match
       │
       v
Deduplicate results
       │
       v
Return top 5 items
       │
       v
Generate grounded response
```

Simple ranking order:

1. Exact normalized `source_url`, title, or tag match among active items.
2. FTS5 match over title, summary, and original content.

Tags are matched from `tags_json`; URLs and tags are not silently assumed to
be part of the existing FTS index. Normalize comparison strings by trimming
whitespace and using case-insensitive comparison; normalize URLs with the
existing public-URL normalization before exact comparison.
Do not implement semantic embeddings, reciprocal-rank fusion, reranking models, graphs, assertion extraction, or complex scoring in the MVP.

When no result is found, Roxy should say that it could not find matching saved information instead of guessing.

---

## User Response Rules

When answering from Brain memory:

- use only retrieved records;
- paraphrase naturally;
- do not expose raw database fields or tool payloads;
- do not reveal internal prompts or hidden reasoning;
- mention uncertainty when records conflict;
- optionally mention the saved item title or URL so the user can identify the source.

Example:

```text
You previously chose SQLite because Roxy is currently a small,
single-user application. I found this in your saved database decision note.
```

---

## File Responsibilities

Keep responsibilities small and reuse existing files where possible.

- `src/knowledge/brain_store.py`
  - schema migration;
  - save item and source fields in one transaction;
  - active FTS queries;
  - pending-item queries.

- `src/knowledge/capture_planner.py`
  - create simple save specifications;
  - always preserve submitted URLs.

- `src/knowledge/brain_analysis.py`
  - remove this module and its analysis persistence and relation generation.

- `src/knowledge/indexing.py`
  - one `asyncio.Queue`;
  - one background coroutine;
  - URL fetching and safe title/summary metadata updates.

- `src/knowledge/retrieval.py`
  - exact lookup;
  - FTS search;
  - deduplicate and return top results.

- `src/knowledge/recall.py`
  - detect clear recall requests;
  - format bounded memory context.

- `src/knowledge/tools.py`
  - expose saved-item search and item detail retrieval.

- `src/handlers/chat.py`
  - remove unconditional Brain lookup;
  - call retrieval only for clear recall requests or an explicit tool call.

- `src/app.py`
  - start and stop the background coroutine.

- `src/reminders/worker.py`
  - continue scheduled-delivery processing;
  - remove the nightly Brain-organization invocation.

- Dashboard files
  - display source URL and source status;
  - preserve archive and delete flows;
  - remove the Brain organization control and knowledge-map/relationship views.

---

## Implementation Phases

## Phase 1: Simplify the Schema

**Outcome:** Brain has only the tables needed to save and recall user information; task delivery has a clearly named scheduling table.

### Tasks

- [ ] Rename `reminder_deliveries` to `scheduled_deliveries` with an idempotent migration that preserves all rows.
- [ ] Drop the old due index and create `scheduled_deliveries_due_index` after the table rename.
- [ ] Rename references, exports, dashboard queries, repository queries, and tests to `scheduled_deliveries`.
- [ ] Remove schema-initialization code and all reads of `brain_settings`, `brain_captures`, `brain_capture_items`, `brain_item_relations`, and `brain_organization_lock`; then export and drop those tables.
- [ ] Remove auto-capture settings, capture provenance, relation generation, Brain organization, and the relationship-map UI.
- [ ] Keep `reminder_worker.py` for scheduled deliveries, but remove its Brain-organization work.
- [ ] Keep `messages`, `brain_items`, FTS5, and task scheduling working without a new Brain table.
- [ ] Add migration tests for renamed scheduled deliveries and the absence of removed Brain tables.

**Acceptance criteria:** The application schema is limited to `messages`, `brain_items`, `scheduled_deliveries`, and the FTS5 virtual table plus SQLite-managed FTS support tables.

---

## Phase 2: Preserve Original Evidence

**Outcome:** Every saved item keeps its original content and URL in `brain_items`.

### Tasks

- [ ] Add nullable `brain_items.source_status` with an idempotent migration.
- [ ] Backfill `legacy` for existing items with a URL.
- [ ] Save original content, URL, and source status on `brain_items` in one transaction.
- [ ] Preserve unreadable URLs instead of dropping them.
- [ ] Delete related `scheduled_deliveries` rows before deleting a Brain item, in the same transaction. Do not change foreign-key enforcement or rebuild the delivery table for this MVP.
- [ ] Keep all task and scheduled-delivery behaviour unchanged.
- [ ] Add tests for migration, repeat startup, new saves, unreadable URLs, archive, and delete.

**Acceptance criteria:** Every existing and new Brain item preserves its source fields, and no scheduled-delivery or task behaviour breaks.

---

## Phase 3: Save First, Enrich Later

**Outcome:** Once capture begins, saving completes before URL fetching or metadata enrichment.

### Tasks

- [ ] Add one in-process `asyncio.Queue` and one runner.
- [ ] Acknowledge the user immediately after the save transaction commits.
- [ ] Move URL fetching and safe title/summary metadata updates into the runner without changing original user fields.
- [ ] Re-enqueue unfinished work during application startup.
- [ ] Set failed items to `unavailable`; re-enqueue only `pending` items at startup and allow an explicit later retry to reset an item to `pending`.
- [ ] Ensure a failed background update leaves the item searchable through FTS5.
- [ ] Add tests for immediate acknowledgement, background success, background failure, and restart recovery.

**Acceptance criteria:** After capture begins, URL work never blocks or removes a saved item. Broken links remain searchable bookmarks.

---

## Phase 4: Intentional FTS Recall

**Outcome:** Roxy searches Brain only when memory is clearly needed.

### Tasks

- [ ] Remove unconditional `build_brain_context` from normal messages and photo captions.
- [ ] Detect clear recall phrases and retain the explicit saved-item search tool as a fallback for natural recall requests.
- [ ] Reuse one retrieval function for Telegram and the dashboard: exact normalized URL, title, and tag lookup followed by FTS.
- [ ] Return no more than five active results.
- [ ] Exclude archived and deleted items.
- [ ] Return a clear no-result response instead of guessing.
- [ ] Add tests for recall, ordinary chat, archived records, and no-result behaviour.

**Acceptance criteria:** Normal chat does not search Brain, while explicit recall remains fast and grounded.

---

## Dashboard and Data Management

Keep dashboard changes minimal:

- show whether the item came from text, URL, photo, voice, file, or legacy data;
- show the source URL when present;
- show `pending`, `ready`, or `unavailable` for link fetching;
- use the same FTS search function as Telegram;
- preserve archive and delete confirmations.

Export and deletion must include `brain_items` and `scheduled_deliveries`.

Deleting a Brain item must explicitly delete its related `scheduled_deliveries`
records first, in the same transaction, before deleting the Brain item.

---

## Testing Requirements

Use the existing standard-library `unittest` setup.

Required focused tests:

- idempotent migration;
- legacy source status for each existing URL item;
- original content preservation;
- immediate save acknowledgement;
- readable and unreadable URL capture;
- background URL update success and failure;
- startup recovery of pending items;
- FTS keyword retrieval;
- exact title, tag, and URL retrieval;
- archived and deleted item exclusion;
- normal chat without automatic Brain retrieval;
- dashboard and Telegram retrieval consistency;
- export and delete cleanup;
- existing task and scheduled-delivery regression tests;
- retained reminder delivery without nightly Brain organization;
- removal of capture-audit, relation, organization, and auto-capture UI paths.

Run the complete suite before handoff:

```bash
python -m unittest discover -s tests -v
```

---

## Out of Scope

Do not build these unless a real requirement appears:

- PostgreSQL;
- a vector database;
- Redis or Celery;
- microservices;
- a new worker process for Brain enrichment;
- embeddings;
- capture audit trails;
- knowledge graphs and stored relations;
- Brain organization jobs or locks;
- database-backed auto-capture settings;
- structured assertion extraction;
- source revision history;
- durable job queues;
- reciprocal-rank fusion;
- LLM reranking;
- automatic memory access for every conversation;
- multiple application-instance coordination.

---

## Final Minimal Architecture

```text
User
 │
 v
Telegram / Dashboard
 │
 v
Roxy Application
 ├── Assistant and tools
 ├── Brain save/search service
 ├── Explicit recall routing
 └── One background asyncio runner
          │
          v
      SQLite + FTS5
      ├── brain_items
      ├── brain_items_fts
      └── scheduled_deliveries
```

This is the recommended MVP:

```text
One Python codebase
One SQLite database
One background coroutine
Existing reminder-delivery process
FTS5 retrieval
No distributed infrastructure
```

---

## Delivery Checklist

- [ ] Existing URL items receive the `legacy` source status.
- [ ] New notes and URLs are saved before any URL enrichment starts.
- [ ] Unreadable URLs remain saved as bookmarks.
- [ ] Original source content is never overwritten by AI metadata.
- [ ] FTS5 works even when a background URL update fails.
- [ ] Normal conversation does not automatically search Brain.
- [ ] Explicit recall returns grounded active records.
- [ ] Dashboard and Telegram share the same retrieval logic.
- [ ] Archive, delete, export, tasks, and scheduled deliveries continue working.
- [ ] Capture audits, relation graphs, Brain organization, and database-backed auto-capture settings are removed.
- [ ] No new database, broker, URL-enrichment worker process, or unnecessary dependency is introduced.
