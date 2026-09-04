# System Overview

**EnterpriseRAG** is an enterprise document management and retrieval-augmented generation
platform. An administrator uploads documents; the system extracts, classifies, chunks, embeds
and indexes them; and a ChatGPT-style interface answers questions grounded strictly in that
corpus, with citations.

## Topology

```
                        ┌──────────────────────────┐
                        │      Next.js  :3000      │
                        │  Dashboard · Upload      │
                        │  Documents  · Chat       │
                        └────────────┬─────────────┘
                                     │  BFF route handlers
                                     │  (secrets stay server-side)
                                     ▼
                        ┌──────────────────────────┐
                        │      FastAPI  :8000      │
                        │  routes → controllers    │
                        │  → services → repos      │
                        └───┬──────────────┬───────┘
                            │              │
              ┌─────────────┘              └──────────────┐
              ▼                                           ▼
    ┌───────────────────┐                      ┌────────────────────┐
    │ PostgreSQL :5432  │                      │  Memurai   :6379   │
    │ source of truth   │                      │  broker · cache    │
    └───────────────────┘                      │  chat context      │
              ▲                                └─────────┬──────────┘
              │                                          │
              │                                          ▼
              │                                ┌────────────────────┐
              │                                │   Celery worker    │
              │                                │   --pool=solo      │
              │                                └─────────┬──────────┘
              │                                          │
              │        ┌──────────────┬──────────────────┼───────────────┐
              │        ▼              ▼                  ▼               ▼
              │  ┌──────────┐  ┌────────────┐   ┌──────────────┐  ┌───────────┐
              │  │ Parsers  │  │ Tesseract  │   │  CrewAI +    │  │ Sentence  │
              │  │ PDF DOCX │  │    OCR     │   │   Gemini     │  │Transformer│
              │  │ PPTX TXT │  │            │   │  (agent 1)   │  │  768-d    │
              │  └──────────┘  └────────────┘   └──────────────┘  └─────┬─────┘
              │                                                          │
              └──────────────────────────────────────────────┐           ▼
                                                             │  ┌──────────────────┐
                                              ┌──────────────┴─▶│  Qdrant  :6333   │
                                              │                 │  documents coll. │
                                       ┌──────────────┐         │  768-d · cosine  │
                                       │Flower  :5555 │         └──────────────────┘
                                       └──────────────┘
```

## Responsibilities

| Component | Owns | Never |
| --------- | ---- | ----- |
| **Next.js** | Rendering, client-side validation (UX only), BFF proxying | Direct database access, holding secrets in the browser bundle |
| **FastAPI** | Authentication, authoritative validation, orchestration, retrieval | Long-running work on the request path |
| **PostgreSQL** | Users, documents, metadata, chunks, conversations, messages, citations, audit | — |
| **Qdrant** | Vectors and filterable payload | Being treated as a source of truth |
| **Memurai/Redis** | Celery broker, cache, recent chat context | Being treated as durable storage (§9) |
| **Celery worker** | Everything after upload: extract, OCR, classify, chunk, embed, index | Serving HTTP |
| **Flower** | Task observability | Being exposed publicly |

## The two request paths

Everything in the system is one of two shapes.

### 1. Upload — fast in, slow behind

```
POST /api/v1/admin/documents/upload
  ├─ authenticate
  ├─ per file: sanitise → extension → MIME → magic bytes → size
  ├─ SHA-256 → in-batch check → database check
  ├─ persist row + blob
  ├─ enqueue Celery task
  └─ respond  ~tens of ms, per-file results, partial success
                         │
                         ▼  (asynchronous, seconds to minutes)
        extract → OCR? → normalise → content-hash duplicate check
              → detect language → classify → metadata → tags
              → chunk → embed → upsert to Qdrant → COMPLETED
```

The split is mandated by §20 and has a consequence worth stating plainly: **a document accepted
at upload can later become `DUPLICATE`**, because content-level duplicate detection requires
extracted text. See
[ADR-005](decisions/ADR-005-duplicate-detection-split.md).

The UI closes the gap by polling `GET /documents/{id}/status` until a terminal state (§44).

### 2. Chat — synchronous, retrieval-bounded

```
POST /api/v1/chat/conversations/{id}/messages
  ├─ load recent context from Redis
  ├─ CrewAI agent 2 — rewrite query, derive filters
  ├─ embed the query (768-d, local)
  ├─ Qdrant search + payload filters + status filter
  ├─ top-K chunks
  ├─ CrewAI agent 3 — compose a grounded answer
  ├─ persist message + citations to PostgreSQL
  ├─ update Redis context
  └─ respond with answer + sources
```

**Never** is the whole corpus sent to the model (§32). Only the top-K retrieved chunks form the
context, and if none are relevant the system says so rather than answering (§34).

## Technology decisions

Each links to the ADR that fixed it.

| Area | Choice | ADR |
| ---- | ------ | --- |
| Roles | Single `admin` | [001](decisions/ADR-001-single-admin-role.md) |
| Agents | CrewAI, exactly three | [002](decisions/ADR-002-crewai-three-agents.md) |
| Embeddings | `all-mpnet-base-v2`, 768-d, local | [003](decisions/ADR-003-local-embeddings.md) |
| OCR | Tesseract, conditional | [004](decisions/ADR-004-tesseract-ocr.md) |
| Duplicates | Split across two moments | [005](decisions/ADR-005-duplicate-detection-split.md) |
| Vector store | Qdrant server, created once | [006](decisions/ADR-006-qdrant-collection-lifecycle.md) |
| Runtime | Native Windows, no Docker | [007](decisions/ADR-007-native-windows-services.md) |
| Design system | Fully rounded, `#15157d`, EnterpriseRAG | [008](decisions/ADR-008-design-system-resolution.md) |

## Where AI is and is not used

Per §8, AI is confined to work that genuinely requires reasoning:

| Uses AI | Deterministic — never AI |
| ------- | ------------------------ |
| Identity-document recognition and field extraction (agent 1) | File, MIME and signature validation |
| Query rewriting (agent 2) | Hashing, duplicate comparison |
| Answer composition (agent 3) | Text extraction, OCR invocation |
| Classification, metadata, tags | Normalisation, chunking |
| — | Embedding, vector upsert |
| — | Status transitions, all database access |

## Known constraints

1. **Memurai's free edition prohibits production use** and requires a restart every 10 days.
   Production needs Linux, WSL2, or an Enterprise licence
   ([ADR-007](decisions/ADR-007-native-windows-services.md)).
2. **Celery on Windows runs `--pool=solo`** — one task at a time. Throughput is one document at
   a time until the pool changes.
3. **`all-mpnet-base-v2` truncates above 384 word-pieces**, silently. Chunk sizing exists
   specifically to stay under it.
4. **Agents 2 and 3 are on the synchronous chat path** and add latency to every message. Both
   need timeouts and non-agent fallbacks.
5. **Tesseract is an external binary.** `pip install` alone does not enable OCR.
6. **`.doc` and `.ppt` are rejected** — legacy binary formats need LibreOffice. `.docx` and
   `.pptx` are supported.
