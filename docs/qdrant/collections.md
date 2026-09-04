# Qdrant — Collections, Payload and Retrieval

Qdrant stores embeddings and their filterable payload. It is **never** a source of truth (§9) —
every fact in the payload is a denormalised copy of a PostgreSQL row.

## Deployment

Server mode, native Windows binary
([ADR-007](../architecture/decisions/ADR-007-native-windows-services.md)):

| Item | Value |
| ---- | ----- |
| Binary | `qdrant.exe` from `qdrant-x86_64-pc-windows-msvc.zip` |
| HTTP / dashboard | `6333` — `http://localhost:6333/dashboard` |
| gRPC | `6334` |
| Storage | `./storage` beside the executable |
| Client | `qdrant-client`, HTTP |

Server mode rather than embedded is a requirement, not a preference: the embedded client takes an
**exclusive lock** on its directory, and both FastAPI (query) and the Celery worker (index) need
access. Embedded mode would deadlock the moment both ran.

## Collection

```python
COLLECTION_NAME = settings.QDRANT_COLLECTION      # "documents"

if not client.collection_exists(COLLECTION_NAME):
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=768, distance=Distance.COSINE),
    )
```

| Property | Value | Fixed by |
| -------- | ----- | -------- |
| Size | **768** | `all-mpnet-base-v2` ([ADR-003](../architecture/decisions/ADR-003-local-embeddings.md)) |
| Distance | **Cosine** | Normalised sentence embeddings |
| Name | Configurable | §27 |

Created **once**, at FastAPI startup, guarded by `collection_exists`.

> 🔴 **`recreate_collection` is forbidden in application code.** It deletes the collection. At
> module scope it wipes the index on every start and every `--reload` — silently, because the
> collection still exists afterwards and simply contains nothing. Destructive rebuilds live in
> `scripts/reindex.py`, run deliberately.
> [ADR-006](../architecture/decisions/ADR-006-qdrant-collection-lifecycle.md)

## Point ids

```python
point_id = uuid5(NAMESPACE_DOCUMENT, f"{document_id}:{chunk_index}")
```

Deterministic, so a retry upserts over the same points rather than doubling them (§23). The same
value is mirrored into `document_chunks.vector_point_id`, giving a two-way link between the
relational row and the vector.

## Payload

Every point carries (§27):

| Field | Type | Purpose |
| ----- | ---- | ------- |
| `document_id` | uuid str | Deletion filter, citation resolution |
| `chunk_id` | uuid str | → `document_chunks.id` |
| `chunk_index` | int | Ordering |
| `text` | str | Returned to the composer agent as context |
| `document_name` | str | Citation label without a database round-trip |
| `document_type` | str | Filter |
| `category_slug` | str | Filter |
| `language` | str | Filter |
| `tags` | list[str] | Filter |
| `page_number` | int·null | Citation — the `Page 4` chip |
| `section` | str·null | Citation |
| `status` | str | **Retrieval eligibility** |
| `created_at` | ISO str | Date filtering |

`text` and `document_name` are duplicated from PostgreSQL deliberately: it makes a search result
directly renderable as a citation with no second query, which matters because the chat path is
synchronous and latency-sensitive.

There is **no owner or ACL field** — there is one role, so nothing would read it
([ADR-001](../architecture/decisions/ADR-001-single-admin-role.md)).

## Payload indexes

Created once alongside the collection. Without them Qdrant scans payloads linearly when
filtering:

```python
for field, schema in [
    ("document_id",   PayloadSchemaType.KEYWORD),
    ("category_slug", PayloadSchemaType.KEYWORD),
    ("document_type", PayloadSchemaType.KEYWORD),
    ("language",      PayloadSchemaType.KEYWORD),
    ("tags",          PayloadSchemaType.KEYWORD),
    ("status",        PayloadSchemaType.KEYWORD),
]:
    client.create_payload_index(COLLECTION_NAME, field, schema)
```

## Retrieval

```python
must = [FieldCondition(key="status", match=MatchValue(value="COMPLETED"))]

if categories: must.append(FieldCondition(key="category_slug", match=MatchAny(any=categories)))
if language:   must.append(FieldCondition(key="language",      match=MatchValue(value=language)))
if tags:       must.append(FieldCondition(key="tags",          match=MatchAny(any=tags)))

hits = client.search(
    collection_name=COLLECTION_NAME,
    query_vector=embed(query),
    query_filter=Filter(must=must),
    limit=settings.RETRIEVAL_TOP_K,          # 5
    score_threshold=settings.RETRIEVAL_MIN_SCORE,   # 0.35
    with_payload=True,
)
```

Three things are load-bearing:

**The `status` filter is always present.** It is §33's permission filtering under a single role,
and it is applied *inside* the query rather than by discarding results afterwards — so a deleted
or failed document never occupies one of the five top-K slots.

**`score_threshold` is set.** Without it, a query unrelated to anything in the corpus still
returns five chunks — the five least-bad — and the composer agent receives irrelevant context
that reads as plausible. The threshold is what makes §34's *"I could not find this information"*
reachable: below it, retrieval legitimately returns nothing.

**Optional filters are added only when present.** An AND of filters the user never asked for is
the classic cause of "search found nothing" — an unrecognised category silently eliminating every
result. Agent 2 proposes filters; a filter it invents that matches no documents would do exactly
that, so proposed filters are validated against the live taxonomy before use.

## Deletion

```python
client.delete(
    collection_name=COLLECTION_NAME,
    points_selector=FilterSelector(
        filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=str(doc_id)))])
    ),
)
```

Called on delete (§30) and **before** re-indexing on reprocess (§29). The second case matters: a
reprocessed document that produced fewer chunks would otherwise leave its old tail chunks
searchable forever — orphans pointing at content that no longer exists.

## Operations

| Concern | Answer |
| ------- | ------ |
| Start | `qdrant.exe` in its own terminal |
| Dashboard | `http://localhost:6333/dashboard` — operational only, never referenced from application code (§27) |
| Persistence | `./storage` beside the binary; survives restarts |
| Rebuild | `scripts/reindex.py` — reads chunks from PostgreSQL, re-embeds, upserts. **No re-parsing**, because chunk text lives in the database |
| Backup | Snapshot API, or copy `./storage` while stopped |
| Outage | Indexing retries 3× then `VECTOR_INDEXING_FAILED`; chat returns `SEARCH_FAILED` 503 |

The rebuild path is why `document_chunks.text` is stored relationally as well as in Qdrant — a
full re-index costs an embedding pass, not an extraction and OCR pass.

## Constraints

1. **768 dimensions are permanent** for this collection. Changing the embedding model requires a
   full rebuild, not a migration.
2. **No reranking in v1.** §32 lists it as optional; the threshold plus top-K is the v1 answer.
3. **Payload duplicates PostgreSQL.** Renaming a document must update the payload too, or
   citations show the old name. Handled in the metadata-edit path
   ([../features/document-management.md](../features/document-management.md)).
4. **No multi-tenancy.** One collection, one corpus.
