# Brain Connection Analysis Design

## Goal

Analyze every request that writes a Brain item before persistence so Roxy stores
concise, searchable records and creates sparse, explainable connections based on
shared entities, domains, and high-confidence semantic relationships.

## Scope

- Apply the same analysis pipeline to explicit captures and automatic Brain
  saves.
- Replace raw-message title and summary fallbacks during normal successful
  analysis with concise structured metadata.
- Extract normalized entities and domains for each saved item.
- Create direct relations when records share a clearly identified entity or
  domain, and inferred semantic relations only when their confidence meets a
  strict threshold.
- Show the relation kind and explanation in the Brain explorer so users can
  distinguish direct evidence from an inference.
- On analysis failure, save one minimal unconnected record rather than losing
  an explicit save.
- Remove legacy migration and compatibility behavior from the Brain feature.
  The user will start from a clean database; this work does not backfill or
  reinterpret historical items.
- Run a daily 3:00 AM `TASK_TIMEZONE` introspection pass over active items
  from the last 30 days and all active unconnected items.

## Non-goals

- Do not create relations merely because two items were saved on the same day,
  have the same type, or share a display group.
- Do not create a background retry queue or reanalysis workflow.
- Do not create synthetic daily-summary Brain items.
- Do not retain model scratch work or hidden reasoning.

## Approaches considered

1. Hybrid structured analysis and semantic comparison (selected): supports
   exact person/project/topic connections while allowing cautious inferred
   links for related wording.
2. Rule-only extraction: predictable, but cannot reliably resolve references
   such as “my girlfriend” and “Ruchi.”
3. Vector-only similarity: flexible, but adds storage and produces less
   explainable links.

## Architecture

`brain_analysis` owns the model-facing structured-analysis contract and its
validation. It receives a proposed Brain record and returns a concise title,
summary, item type, normalized entity and domain tags, and relation candidates.
It never returns chain-of-thought.

Both the explicit capture tool and automatic-save tool call a shared service
before the database write. The service obtains the analyzed record, loads active
items, identifies direct entity/domain overlap, evaluates semantic candidates,
and passes accepted relations with the new item to storage. A relation remains
sparse: it needs a short user-readable explanation and confidence. Direct
matches are marked as evidence; semantic matches are marked as inferred and
must meet the configured strict threshold.

The database remains the source of truth: `brain_items.tags_json` stores
normalized entities/domains and `brain_item_relations` stores actual links.
The explorer renders only stored relations. It may organize the layout by a
domain for readability, but layout groups are not presented as relationships.

## Data flow

1. An explicit capture or automatic capture proposes Brain content.
2. The shared analysis service calls the configured OpenAI model with a strict
   structured response schema.
3. The service validates the response and normalizes tags; invalid analysis is
   treated as unavailable.
4. On success, it compares the new record with active records. Clear entity or
   domain overlap creates a direct relation; high-confidence semantic agreement
   may create an inferred relation.
5. Storage writes the item and its accepted relations atomically.
6. On model/API/schema failure, storage writes one minimal unconnected item.
7. The Brain explorer shows only stored relation edges and labels each as
   direct evidence or inferred topic similarity.
8. At 3:00 AM in `TASK_TIMEZONE`, the existing worker reviews eligible active
   records in bounded batches and refreshes only justified relations.

## Relation rules

| Evidence | Relation label | Requirement |
| --- | --- | --- |
| Shared normalized entity | `same entity` | The same named person, project, place, organization, or other entity appears in both records. |
| Shared explicit domain | `same domain` | The domain is specific and meaningful, not a generic type such as `idea`. |
| Semantic analysis | `related topic (inferred)` | The model provides a concise explanation and confidence at or above the strict threshold. |

For example, “My girlfriend is Ruchi” and “Ruchi’s birthday is 17 June 2005”
share the normalized entity `ruchi`, so they receive a direct `same entity`
relation with the explanation “Both records refer to Ruchi.”

## Error handling

All failures use the shared error utilities. Analysis failures are logged
without exposing private saved content to users, then invoke the minimal
unconnected fallback. Database failures preserve the existing error behavior;
no partial relation set is written.

## Nightly introspection

The nightly worker selects all active records saved in the last 30 days and
all active records with no relation. It processes at most 100 records in
batches of 20, uses the same validation and confidence rules as capture-time
analysis, and never changes item content or creates a summary note. Direct
relations may be added or refreshed. Inferred relations may be refreshed,
downgraded, or removed only when the evaluated pair no longer has sufficient
evidence. A failed run is logged and does not interfere with reminder delivery;
the next scheduled run retries naturally.

## Testing

- Explicit and automatic saves both call the shared analysis service.
- The Ruchi example produces a direct stored relation with an explanation.
- A strong semantic candidate produces an inferred, labeled relation.
- Low-confidence semantic candidates and shared timestamps/types do not create
  relations.
- Analysis failure saves a fallback item without relations.
- The Brain page renders stored direct and inferred relations, and does not
  claim layout grouping is a relation.
- Legacy migration behavior is absent from the clean-database path.
- The 3:00 AM job covers recent and unconnected active records without adding
  synthetic Brain items or blocking reminder delivery.
