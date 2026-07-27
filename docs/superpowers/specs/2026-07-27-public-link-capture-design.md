# Public Link Capture Design

## Goal

When a user explicitly asks Roxy to save links or a compound thought, Roxy
should first analyze its structure and then save useful, searchable knowledge
rather than only raw text or URLs. Later retrieval must include the original
clickable link for every matching saved source.

## Scope

- Support public `http` and `https` links from any source type, including
  articles, public documents, public Google Drive files, and reel/video pages.
- Support natural-language web research through a provider-backed web-search
  tool. Roxy searches the internet when the user asks it to find, research, or
  verify something; it does not build or operate a separate search engine.
- Analyze each explicit save request before writing data. A simple, indivisible
  thought becomes one brain item. A compound request becomes one capture bundle
  plus distinct, linked atomic brain items for each independently useful source,
  fact, decision, task, question, or idea.
- Create one `reference` brain item per distinct link in a compound explicit
  save request. A message that includes several links must produce distinct
  items and a result for each source.
- Fetch each URL, extract available readable text and source metadata, then use
  that material to generate the saved title, summary, tags, and content.
- Keep the canonical source URL in `brain_items.source_url`.
- Make extracted material searchable through the existing SQLite FTS index.
- Return retrieved items with their original source URLs as clickable links.
- Record timezone-aware capture date and time for every request and saved item,
  plus the source/publication time when it can be reliably extracted.
- Build explicit relationships between saved items so later questions can show
  ongoing themes, connected projects, decisions, sources, and changes over time.

## Non-goals

- Do not authenticate with Google, Instagram, Drive, or any other provider.
- Do not bypass paywalls, login requirements, bot checks, or access controls.
- Do not download or retain video/audio media. A public reel/video can only be
  analyzed when its page exposes usable text such as a title, description, or
  transcript.
- Do not change automatic second-brain capture for ordinary messages.
- Do not store raw model chain-of-thought or hidden scratch reasoning.

## Data flow

1. The intent router identifies an explicit request to save a message and makes
   the capture tool available.
2. The capture planner classifies the request as either a single atomic item or
   a compound capture. It identifies durable items, source links, and their
   relationships before anything is written.
3. The tool creates a capture record for the explicit request, validates and
   de-duplicates URLs, then processes each source independently.
4. For a public source, the ingestion service requests the URL with bounded
   timeouts and size limits, extracts safe readable text and metadata, and
   generates a compact saved reference.
5. For an inaccessible or unsupported source, the service first asks the user
   for a short manual description when the URL/metadata is insufficient to
   classify it. If the user declines or does not provide one, it creates a
   clearly labeled bookmark-style reference instead.
6. Each child brain item stores a concise conclusion, supporting source excerpts
   or notes, a user-facing capture rationale, and its relationship to the
   parent capture. It never stores raw model scratch work.
7. The relationship builder compares the new atomic items with existing active
   items and records only meaningful, evidence-based links, such as `about`,
   `supports`, `contradicts`, `continues`, `decides`, `updates`, or
   `source_for`. It records the relationship explanation and confidence.
8. The tool returns a per-link and per-item result. Roxy confirms how many
   sources were analyzed and which were saved as bookmarks.
9. Search results and later answers include title, summary, capture context,
   and source URL so Roxy can give the user the original clickable links.

An unambiguous direct request such as "save this", "save these links", or
"remember this" writes immediately and then reports what was saved. It does
not require a second confirmation. Roxy asks one clarifying question only when
the requested material is ambiguous or an unreadable source needs a manual
description. Permanent deletion alone requires explicit confirmation.

### Natural web research flow

1. A user asks naturally, for example, "Find the best recent articles about
   local AI models" or "Look up whether this library supports X."
2. The intent router selects a narrow `web_research` capability and exposes only
   the web-search tool to the model.
3. The model sends a focused search query; the provider performs the actual web
   search and returns result titles, snippets, URLs, and available source
   content. Roxy does not crawl, index, rank, or host the internet itself.
4. Roxy answers concisely with clickable source links and clearly separates
   sourced facts from its inference.
5. Search results are ephemeral by default. Roxy saves sources or conclusions
   to the second brain only when the user explicitly asks to save them, then
   uses the normal capture planner and source-provenance rules.

## Components

### `src/knowledge/link_capture.py`

Owns URL extraction, validation, canonical de-duplication, public-page
retrieval, content-type detection, metadata/readable-text extraction, and
bounded text normalization. It exposes a small result type that distinguishes
analyzed public content, a source requiring manual description, and a bookmark
fallback.

### `src/knowledge/brain_tools.py`

Adds an explicit capture tool. It invokes the capture planner and link capture
per URL, creates one linked brain item per atomic result with
`source_type = "link"` and `item_type = "reference"` for sources, and reports
partial success without discarding successfully processed links.

### `src/knowledge/web_search.py`

Owns the small adapter around the selected provider's web-search tool: query
validation, response normalization, source attribution, and user-safe error
results. It does not implement a crawler, scraper fleet, index, or ranking
system. Provider-specific credentials and limits remain configuration, not
prompt text.

### `src/knowledge/brain.py`

Adds capture-bundle storage while retaining the existing `brain_items` as the
searchable atomic knowledge records. Search result serialization gains
`source_url` and capture context, allowing the model to include links and group
related saved material in a later response.

### Capture storage model

`brain_captures` represents one explicit save request. It stores the original
user request, a concise overall analysis, the user-facing capture rationale,
capture mode, and a timezone-aware `captured_at` timestamp. `brain_capture_items`
maps a capture to its atomic `brain_items`, recording item order, relationship
type, source URL, and whether the item is analyzed content or a bookmark
fallback.

`brain_items` retains its existing creation and update timestamps and gains an
optional `source_published_at` timestamp when a reliable article/document date
is available. `brain_item_relations` stores a directed relationship between two
items, its type, short evidence-based explanation, confidence, creation time,
and whether it was inferred or explicitly stated by the user. Duplicate
relationships are merged instead of repeatedly saved.

The capture planner uses these rules:

| Request shape | Saved data |
| --- | --- |
| One durable fact, decision, idea, or single readable link | One capture and one linked brain item. |
| Several links | One capture and one linked reference item per distinct URL. |
| A message containing independently retrievable facts, tasks, decisions, or ideas | One capture and one linked brain item per atomic concept. |
| Context meaningful only as a group | One capture and one linked overview item; do not manufacture weak fragments. |

`brain_items.content` stores normalized source material and useful analysis;
`summary` stores the short retrieval answer; `title` remains a compact label.
The capture rationale explains the durable reason for the record, for example
"explicitly saved public article, contains product research". It is not hidden
model reasoning.

Relationships are deliberately sparse. Shared tags alone are useful for the
thought map but do not become permanent relationships. Roxy creates a stored
relationship only when the content gives a clear reason, and it includes the
short explanation needed for later introspection. This prevents a noisy,
over-connected graph.

### Prompts and tool routing

The router uses small, mutually clear intent capabilities: `general`,
`brain_capture`, `brain_management`, `web_research`, `reminders`, and
`expenses`. It selects the narrowest matching capability and exposes only that
capability's tool definitions. This keeps classification predictable and avoids
giving the model a long, confusing menu of unrelated tools.

Explicit "save this/link/these" requests with URLs receive the capture tool,
instead of relying on a generic brain tool to infer content it cannot access.
Natural requests to find, research, check, compare, or verify current external
information receive the web-search tool. A request to both research and save
may use web search first, then the capture tool only after the user explicitly
asks to save the resulting knowledge. Prompt instructions require analysis
before writes, one source result per URL, and returning source links when
recalling saved references. They require a concise evidence-based conclusion
and capture rationale, never raw internal reasoning.

## Error handling and safety

- Accept only public `http`/`https` URLs. Reject local, malformed, and unsafe
  destinations before fetching.
- Protect against server-side request forgery by resolving the host and refusing
  loopback, private, link-local, multicast, and reserved IP addresses.
- Set redirect, timeout, and maximum-response-size limits. Revalidate every
  redirect target using the same rules.
- Treat any access failure, non-text response, extraction failure, or empty
  readable result as a manual-description request when classification needs
  context, or otherwise as a successful bookmark fallback. Neither outcome
  prevents other links from being saved.
- Use the repository's shared error utilities for exception handling.
- Store only extracted text needed for search and analysis, bounded to a safe
  maximum; never persist credentials or authorization headers.
- Use UTC ISO 8601 timestamps for storage and the user's configured timezone
  when showing when a thought was captured. Never invent a source publication
  date.

## User-facing behavior

- "Save this https://example.com/article" saves an analyzed reference and
  confirms it with its original URL.
- "Save these: URL A, URL B" creates two independently searchable references
  and identifies any source saved only as a bookmark.
- If Roxy cannot understand a source from its public page, URL, or metadata, it
  asks: "What should I remember about this link?" and saves the user's short
  description as the source note.
- "What did I save about X?" gives concise results and clickable source URLs,
  even when the original content was unavailable.
- "What have I been thinking about lately?" groups recent captures by their
  relationships and time, showing active themes, connected items, and the dates
  they entered the second brain.

## Brain page experience

The authenticated `/brain` page becomes a useful second-brain explorer rather
than a dots-only map. It follows the existing dashboard's typography, colors,
spacing, cards, controls, and authenticated-page layout so it feels like part
of the same product.

### Main views

- **Timeline:** recent captures grouped by day, with saved time, source date
  when available, capture rationale, and linked items. This answers, "What was
  I focused on recently?"
- **Connections:** a 2D grouped relationship explorer with labeled edges and an
  adjacent details panel. Group items by active theme/project and date, then
  show the selected item and its direct neighbors instead of an unreadable map
  of every item. Selecting an item shows why it connects to each neighbor, the
  relationship type, confidence, and relevant source links. The explorer must
  not rely on color or dots alone.
- **Items:** a searchable, filterable card/list view showing title, summary,
  type, capture date, source link, tags, relation count, and bookmark/analyzed
  status. It remains the accessible equivalent of the graph.

### Item actions

Each item has an overflow/action control with **Open source**, **View related
items**, **Archive**, and **Delete**. Delete is permanent, so the page requires
an explicit confirmation dialog naming the affected item. Archive removes an
item from active views but preserves its data and connections for recovery.
After a successful action, the UI updates the timeline, list, selected-item
panel, and graph consistently.

### Responsive and accessible behavior

- On mobile, use list/timeline-first navigation; the relationship graph opens
  as a focused view rather than a cramped canvas.
- All graph relationships have equivalent text in the selected-item panel and
  list. Keyboard users can search, select items, open source links, and confirm
  destructive actions without interacting with the graph.
- Empty states explain how to save a thought or link. Loading, ingestion,
  archive, and deletion failures have concise, recoverable messages.
- Do not build a 3D graph for this version. A 2D grouped explorer is easier to
  analyze, search, label, navigate, and use accessibly; 3D can be considered as
  an optional future visual-only mode.

### Backend support

Authenticated brain data includes captures, item timestamps, source links,
relations, and relation explanations. Add authenticated item archive/delete
endpoints that validate the item ID and require a delete-confirmation request;
the page uses these endpoints rather than embedding destructive behavior in
untrusted client data.

## Three implementation phases

### Phase 1: Capture and source ingestion

Implement safe public-link fetching, readable-text extraction, manual
description fallback, one-item versus compound-capture planning, and per-link
partial success. Add the provider-backed natural web-search adapter and clean
intent routing so web research is selected only when requested. Add tests for
public, inaccessible, unsupported, multiple URLs, web-search attribution, and
intent/tool selection.

### Phase 2: Time-aware connected second brain

Add capture records, capture-to-item mappings, source publication time, sparse
item relationships, and relationship explanations/confidence. Extend search and
recall responses to return parent context, timestamps, relations, and original
clickable source URLs. Add migration and relationship-quality tests.

### Phase 3: Brain explorer UI and lifecycle controls

Redesign `/brain` with timeline, accessible connections explorer, richer item
view, filters, and dashboard-consistent styling. Add authenticated archive and
delete actions with explicit deletion confirmation, then verify desktop/mobile
behavior and the full regression suite.

## Testing

- URL extraction, de-duplication, and URL-safety checks.
- Natural-language web-research requests select only the web-search tool; brain,
  reminder, and expense requests do not receive it unless their intent requires
  it.
- Web-search responses preserve result URLs, distinguish sources from Roxy's
  inference, and are not automatically persisted.
- HTML metadata/body extraction and content-size/time-limit behavior.
- Public-source save creates a searchable reference with extracted content and
  `source_url`.
- Private/unsupported/failing source asks for a manual description when needed
  and otherwise creates a bookmark fallback with a clear saved note.
- A multi-link request creates separate items and preserves partial success.
- Simple and compound-save planner tests verify one-item versus multi-item
  decisions, parent/child relationships, and avoidance of meaningless splits.
- Stored rationale and analysis contain no raw chain-of-thought fields.
- Capture and item timestamps are timezone-aware; reliable source publication
  dates are preserved separately from save time.
- Relationship creation is tested for meaningful links, duplicate merging,
  confidence/explanations, and rejection of relationships based only on tags.
- Search tool results include `source_url` and capture context; chat reply
  rendering includes each matching source URL.
- Brain page tests cover timeline data, relation explanations, safe item action
  endpoints, confirmation-required deletion, archive behavior, and safe source
  link rendering.
- Browser checks cover mobile/desktop layout, keyboard operation, and text
  alternatives for graph relationships.
- Existing second-brain, chat, and complete test suites remain green.

## Acceptance criteria

1. Roxy analyzes the structure of every explicit save request before writing it
   as one item or a capture with multiple atomic items.
2. Explicitly saving multiple links yields a separate linked reference record
   for each distinct URL and one parent capture record.
3. Public pages contribute readable extracted text to the saved content and can
   be found later with the existing search flow.
4. Private or unsupported links prompt for a short manual description when that
   context is needed; otherwise they are retained as clearly labeled bookmarks.
5. Stored explanations are concise conclusions, source evidence, relationships,
   and a user-facing capture rationale, never raw model reasoning.
6. Later recall supplies original source links with the saved summaries and
   enough capture context to make the result understandable.
7. Failed or inaccessible links do not stop other links in the same request.
8. Every capture and item can be placed on a reliable timeline, with source
   publication time shown separately when available.
9. Roxy can explain meaningful connections among saved items and summarize
   recent themes without treating every shared tag as a relationship.
10. The Brain page shows connections with labels and explanations, richer
    timeline/item data, and a responsive accessible alternative to the graph.
11. Users can archive an item or permanently delete a named item only after an
    explicit confirmation, without leaving inconsistent graph or timeline data.
12. Natural web research uses a provider-backed search tool, returns attributed
    clickable sources, and never creates an in-house crawler or silently saves
    search results.
13. Intent classification exposes the model only the small relevant set of
    tools for each request.
