# ADR-006 — Qdrant Collection Lifecycle

**Status:** Accepted · **Date:** 2026-08-21 · **Decides:** spec §23, §27

## Decision

The `documents` collection is created **once, only if absent**, and never recreated by
application code.

```python
if not client.collection_exists(COLLECTION_NAME):
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=768, distance=Distance.COSINE),
    )
```

## Explicitly forbidden

```python
client.recreate_collection(...)      # never in application code
init_collection()                    # never at module scope
```

`recreate_collection` **deletes an existing collection**. Called at import time it wipes the
index on every application start and on every `--reload` cycle during development — silently,
because the collection still exists afterwards and simply contains nothing.

This has been observed in a sibling project in this codebase and cost real debugging time. It is
recorded here so it is not reintroduced.

Collection creation is a **startup step**, run once by the FastAPI lifespan handler, guarded by
`collection_exists`. A destructive rebuild belongs in `scripts/reindex.py`, run deliberately by
an operator, never on import.

## Point IDs — idempotency

§23 requires that a Celery retry must not create duplicate vectors. The point ID is therefore
**deterministic**:

```
point_id = uuid5(NAMESPACE_DOCUMENT, f"{document_id}:{chunk_index}")
```

Re-running the task upserts over the same IDs. A retry after a partial failure converges to the
correct state instead of doubling the chunks.

## Deletion

Deleting or reprocessing a document removes its vectors with a payload filter on `document_id`
**before** re-indexing, so a shorter re-processed document cannot leave orphaned tail chunks
searchable (§29's "never leave stale vectors searchable").

## Consequences

- Vectors survive restarts.
- `scripts/reindex.py` is the only path that may drop the collection.
- Changing the embedding model ([ADR-003](ADR-003-local-embeddings.md)) requires that script.
