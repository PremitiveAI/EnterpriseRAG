# ADR-003 — Local Sentence-Transformers Embeddings

**Status:** Accepted · **Date:** 2026-08-21 · **Decides:** spec §16, §27

## Context

The spec names Gemini as the AI model but never specifies the embedding model. The choice fixes
the Qdrant vector dimension permanently — changing it later means dropping and rebuilding the
collection.

## Decision

**`sentence-transformers/all-mpnet-base-v2`**, run locally.

| Property | Value |
| -------- | ----- |
| Dimension | **768** |
| Distance | **Cosine** |
| Cost | None — no API calls |
| Network | Not required after first download |
| Model size | ~420 MB, cached under `HF_HOME` |
| Max sequence | 384 word-pieces — **longer text is silently truncated** |

## Consequences

- **Chunk size must respect the 384-token window.** A chunk longer than that loses its tail
  *silently* — no error, no warning. Chunking targets ~1,000 characters precisely so the
  encoder never truncates. See [features/document-processing.md](../../features/document-processing.md).
- The model loads into memory in the **Celery worker** (indexing) and in the **FastAPI process**
  (query embedding). Budget ~500 MB resident in each. Load once at startup, never per request.
- Embedding is CPU-bound and blocking. In FastAPI it must run inside a thread executor so it
  does not stall the event loop.
- Documents and queries **must** use the same model. If it is ever changed, every vector must be
  regenerated — a full re-index, not a migration.
- The Qdrant collection is created with `size=768, distance=Cosine` and must not be recreated at
  import time. See [ADR-006](ADR-006-qdrant-collection-lifecycle.md).

## Alternatives rejected

**Gemini `gemini-embedding-001` (3072-d).** Higher quality, but per-call cost on every chunk of
every document and on every chat message, plus a hard network dependency in the ingest path.
Rejected by the user.
