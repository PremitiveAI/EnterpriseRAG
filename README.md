# EnterpriseRAG

| Application   | Technology                                                                          | Location                 |
| ------------- | ----------------------------------------------------------------------------------- | ------------------------ |
| Backend       | Python 3.11 · FastAPI 0.141 · SQLAlchemy 2.0 · PostgreSQL · Qdrant · Celery · Redis | [`backend/`](backend/)   |
| Frontend      | Next.js 16 (App Router) · React 19 · TypeScript · Tailwind v4                       | [`frontend/`](frontend/) |
| Documentation | 32 files including 8 ADRs                                                           | [`docs/`](docs/)         |

---

## Description

Enterprise document management with retrieval-augmented chat. An administrator uploads
documents; the system extracts their text (OCR when there is no text layer), classifies and
tags them with an LLM, chunks and embeds them locally, and indexes them in a vector store.
A chat interface then answers questions strictly from that corpus, with citations, and
refuses when nothing in the corpus supports an answer.

---

## Application Showcase

#### 1. Authentication & Control Center

![Login Screenshot](docs/screenshots/01-login.jpg)

#### 2. System Dashboard & Analytics

![Dashboard Screenshot](docs/screenshots/02-dashboard.jpg)

#### 3. Document Repository & Metadata Extraction

|                 Repository List                 |                 Details & Metadata                  |
| :---------------------------------------------: | :-------------------------------------------------: |
| ![Documents](docs/screenshots/03-documents.jpg) | ![Details](docs/screenshots/04-document-detail.jpg) |

#### 4. Batch Upload & Grounded RAG Chat

|           Batch Pipeline Upload           |          Chat with Citations          |
| :---------------------------------------: | :-----------------------------------: |
| ![Upload](docs/screenshots/05-upload.jpg) | ![Chat](docs/screenshots/06-chat.jpg) |

---

## Topics

`rag` `retrieval-augmented-generation` `fastapi` `python` `nextjs` `react` `typescript`
`postgresql` `sqlalchemy` `qdrant` `vector-database` `celery` `redis` `crewai` `gemini`
`sentence-transformers` `embeddings` `ocr` `tesseract` `document-management`
`semantic-search` `tailwindcss`

## Table of contents

- [Overview](#overview)
- [Key features](#key-features)
- [Technology stack](#technology-stack)
- [System architecture](#system-architecture)
- [User flow](#user-flow)
- [Document upload and processing flow](#document-upload-and-processing-flow)
- [Chat / RAG flow](#chat--rag-flow)
- [AI architecture](#ai-architecture)
- [Database architecture](#database-architecture)
- [Vector database](#vector-database)
- [Redis](#redis)
- [Celery](#celery)
- [Flower](#flower)
- [API overview](#api-overview)
- [Project structure](#project-structure)
- [Application screenshots](#application-screenshots)
- [Installation](#installation)
- [Environment configuration](#environment-configuration)
- [Running the application](#running-the-application)
- [Testing](#testing)
- [Error handling](#error-handling)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Future improvements](#future-improvements)

---

## Overview

**The problem.** An organisation accumulates policies, contracts, payroll records and identity
documents in shared folders. Finding a specific clause means knowing which file it is in. Full-text
search fails when the question is phrased differently from the document, and asking a general
LLM produces confident answers that are not in any document.

**The approach.** Index the corpus semantically, retrieve only the passages that clear a relevance
threshold, and answer from those passages alone. When retrieval returns nothing, say so rather
than answer — a wrong answer about a leave policy is worse than no answer.

**Target user.** A single administrator (see
[ADR-001](docs/architecture/decisions/ADR-001-single-admin-role.md)) who owns the corpus: uploads,
curates, and queries it. There are no per-document permissions and no end-user role.

**Major modules**

| Module      | Responsibility                                                          |
| ----------- | ----------------------------------------------------------------------- |
| `auth`      | Login, JWT issue and refresh, audit logging                             |
| `documents` | Upload, validation, the processing pipeline, list/edit/delete/reprocess |
| `chat`      | Conversations, retrieval, grounded answers, citations                   |
| `ai`        | The three agents plus classification, metadata and tag enrichment       |
| `vector`    | Embeddings, Qdrant client, index and search                             |
| `workers`   | Celery app and the document-processing task                             |

---

## Key features

| Feature             | Description                                                                    | Component | Status          |
| ------------------- | ------------------------------------------------------------------------------ | --------- | --------------- |
| Authentication      | JWT bearer + httpOnly refresh cookie, Redis revocation denylist                | Backend   | Implemented     |
| Single admin role   | One role, no per-document ACLs (ADR-001)                                       | Backend   | Implemented     |
| Multi-file upload   | Partial batch success with per-file results                                    | Backend   | Implemented     |
| File validation     | Extension, MIME, magic-byte signature, size, filename sanitisation             | Backend   | Implemented     |
| Duplicate detection | Two levels — file hash at upload, content hash after extraction (ADR-005)      | Backend   | Implemented     |
| Text extraction     | PDF, DOCX, PPTX, TXT, page-aware                                               | Backend   | Implemented     |
| OCR                 | Tesseract + OpenCV preprocessing, only when a page has no text layer (ADR-004) | Backend   | Implemented     |
| AI classification   | Category, title, description and tags from a configurable taxonomy             | AI        | Implemented     |
| Identity extraction | PAN / Aadhaar recognition with Verhoeff and shape validation                   | AI        | Implemented     |
| Local embeddings    | `all-mpnet-base-v2`, 768-d, no per-document API cost (ADR-003)                 | Backend   | Implemented     |
| Vector indexing     | Qdrant, deterministic point ids, payload filters                               | Qdrant    | Implemented     |
| Async processing    | 14-stage pipeline with per-stage status and explicit retry                     | Celery    | Implemented     |
| Status polling      | Lightweight endpoint with derived progress percentage                          | Backend   | Implemented     |
| Document management | Search, filter, sort, paginate, edit, soft delete, reprocess, download         | Backend   | Implemented     |
| Metadata-only edit  | Updates the Qdrant payload without re-embedding                                | Backend   | Implemented     |
| RAG chat            | Grounded answers with resolvable citations                                     | AI/RAG    | Implemented     |
| Grounded refusal    | HTTP 200 with `is_grounded: false` when nothing clears the threshold           | AI/RAG    | Implemented     |
| Adaptive formatting | Answer shape chosen per question — sentence, prose, list, table, code          | AI/RAG    | Implemented     |
| Conversations       | Lazy creation, date-grouped sidebar, rename, soft delete                       | Backend   | Implemented     |
| Context memory      | Last N turns in Redis, rebuilt from PostgreSQL on a miss                       | Redis     | Implemented     |
| PII masking         | Validated PAN/Aadhaar masked in logs, audit rows and chat answers              | Backend   | Implemented     |
| Rate limiting       | Per-IP login, per-user upload/chat/default                                     | Backend   | Implemented     |
| Security headers    | `nosniff`, `DENY`, CSP, Referrer-Policy; HSTS in production                    | Backend   | Implemented     |
| Audit trail         | Append-only log of every mutation with user, request id and IP                 | Backend   | Implemented     |
| Task monitoring     | Worker and task dashboard                                                      | Flower    | Implemented     |
| Streaming answers   | Answers arrive whole                                                           | Frontend  | Not implemented |
| PII reveal action   | Masking is applied; an audited "reveal" is not built                           | Frontend  | Not implemented |

---

## Technology stack

| Technology                          | Version                     | Purpose                                                              |
| ----------------------------------- | --------------------------- | -------------------------------------------------------------------- |
| **FastAPI**                         | 0.141.1                     | Backend HTTP API                                                     |
| **Uvicorn**                         | —                           | ASGI server                                                          |
| **Pydantic / pydantic-settings**    | 2.12.5                      | Request validation, typed configuration                              |
| **SQLAlchemy**                      | 2.0.36                      | ORM, typed `Mapped` models                                           |
| **Alembic**                         | 1.14.0                      | Database migrations                                                  |
| **PostgreSQL**                      | 18.x                        | Source of truth for all business data                                |
| **Qdrant**                          | server 1.19 · client 1.12.2 | Vector store and similarity search                                   |
| **sentence-transformers**           | 3.3.1                       | Local embeddings (`all-mpnet-base-v2`)                               |
| **Redis / Memurai**                 | client 5.2.1                | Celery broker and backend, chat context, rate limits, token denylist |
| **Celery**                          | 5.4.0                       | Background document processing                                       |
| **Flower**                          | 2.0.1                       | Celery worker and task monitoring                                    |
| **CrewAI**                          | 1.15.17                     | Agent orchestration for the two chat-path agents (ADR-002)           |
| **google-generativeai**             | 0.8.3                       | Direct Gemini calls for enrichment and identity extraction           |
| **Tesseract**                       | 5.5.3 (system binary)       | OCR                                                                  |
| **pytesseract / OpenCV / Pillow**   | —                           | OCR invocation and image preprocessing                               |
| **PyMuPDF**                         | 1.25.1                      | PDF text extraction and page rasterisation                           |
| **python-docx / python-pptx**       | —                           | DOCX and PPTX extraction                                             |
| **langdetect**                      | —                           | Language detection                                                   |
| **bcrypt / PyJWT**                  | —                           | Password hashing, token signing                                      |
| **Next.js**                         | 16.3                        | Frontend, App Router, BFF route handlers                             |
| **React**                           | 19.2                        | UI                                                                   |
| **Tailwind CSS**                    | v4                          | CSS-first theming via `@theme`                                       |
| **react-markdown / remark-gfm**     | —                           | Renders the composer's Markdown answers                              |
| **lucide-react**                    | —                           | Icons                                                                |
| **pytest**                          | 8.3.4                       | Backend tests                                                        |
| **vitest / @testing-library/react** | 2.1                         | Frontend tests                                                       |

---

## System architecture

Every service runs natively on Windows — no Docker
([ADR-007](docs/architecture/decisions/ADR-007-native-windows-services.md)).

The browser never holds a backend token. Pages call same-origin Next.js route handlers, which
attach the bearer token server-side from an httpOnly cookie and forward to FastAPI.

```mermaid
flowchart TB
    B["Browser<br/>localhost:3000"]

    subgraph NEXT["Next.js 16 · port 3000"]
        PAGES["Server components<br/>login · dashboard · documents · chat · upload"]
        BFF["Route handlers (BFF)<br/>/api/**"]
    end

    subgraph API["FastAPI · port 8000"]
        MW["Middleware<br/>SecurityHeaders → RequestID → CORS → Auth → RateLimit"]
        RT["Routers<br/>auth · documents · config · chat"]
        SVC["Services<br/>upload · pipeline · management · chat · retrieval"]
    end

    PG[("PostgreSQL 5432<br/>10 tables")]
    RD[("Redis / Memurai 6379<br/>broker · cache · limits")]
    QD[("Qdrant 6333<br/>documents · 768-d cosine")]
    FS["Local storage<br/>backend/storage/YYYY/MM/"]

    subgraph WORKER["Celery worker · solo pool"]
        TASK["documents.process"]
        PIPE["14-stage pipeline"]
    end

    FL["Flower · 127.0.0.1:5555"]
    EMB["sentence-transformers<br/>all-mpnet-base-v2 · local"]
    GEM["Google Gemini<br/>via CrewAI and the SDK"]
    OCR["Tesseract 5.5<br/>+ OpenCV"]

    B -->|"same-origin fetch"| BFF
    B --> PAGES
    PAGES -->|"server-side, bearer attached"| API
    BFF -->|"bearer attached"| MW
    MW --> RT --> SVC

    SVC --> PG
    SVC --> RD
    SVC --> QD
    SVC --> FS
    SVC -->|"enqueue"| RD

    RD -->|"consume"| TASK --> PIPE
    PIPE --> FS
    PIPE --> OCR
    PIPE --> EMB
    PIPE --> GEM
    PIPE --> PG
    PIPE --> QD

    SVC -->|"agents 2 and 3"| GEM
    SVC --> EMB
    FL -.->|"reads broker"| RD
```

**Middleware order** is written in reverse in `create_app()`; a request travels
`SecurityHeaders → RequestID → CORS → Auth → RateLimit → route`. Rate limiting sits _inside_ auth
deliberately, so a per-user limit keys on the real identity and an unauthenticated caller receives
`401` rather than a confusing `429`.

---

## User flow

```mermaid
flowchart TD
    A["Open localhost:3000"] --> B{"httpOnly cookie present?"}
    B -->|No| C["/login"]
    C --> D["POST /api/auth/login<br/>route handler stores the token server-side"]
    D --> E["/dashboard"]
    B -->|Yes| E

    E --> F["/upload"]
    E --> G["/documents"]
    E --> H["/chat"]

    F --> F1["Drag or select files"]
    F1 --> F2["Client validation from<br/>GET /config/upload-limits"]
    F2 --> F3["POST /admin/documents/upload"]
    F3 --> F4["Per-file result:<br/>QUEUED · DUPLICATE · REJECTED"]
    F4 --> F5["Poll /status until terminal"]

    G --> G1["Search · filter · sort · paginate<br/>state lives in the URL"]
    G1 --> G2["Row opens a detail drawer"]
    G2 --> G3["Edit metadata"]
    G2 --> G4["Download original"]
    G2 --> G5["Reprocess"]
    G2 --> G6["Delete — confirm by filename"]

    H --> H1["New chat, or pick from the sidebar"]
    H1 --> H2["Ask a question"]
    H2 --> H3{"Anything retrieved<br/>above threshold?"}
    H3 -->|Yes| H4["Grounded answer + source chips"]
    H3 -->|No| H5["Refusal — no chips, HTTP 200"]
    H4 --> H6["Click a chip → the document"]
```

---

## Document upload and processing flow

Upload is synchronous and fast; everything expensive happens in Celery. The HTTP response never
waits for extraction, OCR or embedding.

**Accepted formats**

| Class                         | Extensions                   | Size limit |
| ----------------------------- | ---------------------------- | ---------- |
| Documents                     | `pdf`, `docx`, `pptx`, `txt` | 20 MB      |
| Images                        | `jpg`, `jpeg`, `png`, `webp` | 2 MB       |
| Rejected with a specific code | `doc`, `ppt`                 | —          |

Batch limit: 20 files per request.

```mermaid
flowchart TD
    U["POST /admin/documents/upload<br/>multipart, up to 20 files"] --> V1["Filename sanitised<br/>path components stripped"]
    V1 --> V2["Extension allowed?"]
    V2 -->|"doc / ppt"| R1["REJECTED<br/>LEGACY_FORMAT_UNSUPPORTED"]
    V2 -->|"unknown"| R2["REJECTED<br/>INVALID_FILE_TYPE"]
    V2 -->|Yes| V3["Magic-byte signature<br/>matches the extension?"]
    V3 -->|No| R3["REJECTED<br/>FILE_SIGNATURE_MISMATCH"]
    V3 -->|Yes| V4["Size within class limit,<br/>and not empty?"]
    V4 -->|No| R4["REJECTED<br/>FILE_TOO_LARGE / FILE_EMPTY"]
    V4 -->|Yes| V5["SHA-256 of the bytes"]
    V5 --> V6{"Hash already live?"}
    V6 -->|Yes| R5["DUPLICATE<br/>level 1 — nothing stored"]
    V6 -->|No| S1["Row inserted, then blob written<br/>to storage/YYYY/MM/uuid.ext"]
    S1 --> S2["COMMIT"]
    S2 --> S3["Enqueue documents.process<br/>after the commit"]
    S3 --> S4["200 with per-file results"]

    S3 -.->|"Redis"| P0

    subgraph PIPE["Celery — 14 stages"]
        P0["PROCESSING · load"] --> P1["Blob still present?"]
        P1 -->|No| F1["FAILED<br/>STORAGE_FILE_MISSING"]
        P1 -->|Yes| P2["EXTRACTING<br/>PDF · DOCX · PPTX · TXT"]
        P2 --> P3{"Page has a text layer?"}
        P3 -->|No| P4["OCR<br/>300 DPI raster → greyscale →<br/>denoise → adaptive threshold"]
        P3 -->|Yes| P5
        P4 -->|"Tesseract absent"| F2["FAILED<br/>OCR_UNAVAILABLE"]
        P4 --> P5["Text assembled"]
        P5 --> P6{"Any text at all?"}
        P6 -->|No| F3["FAILED<br/>NO_TEXT_EXTRACTED"]
        P6 -->|Yes| P7["Content hash of normalised text"]
        P7 --> P8{"Matches a COMPLETED document?"}
        P8 -->|Yes| D1["DUPLICATE<br/>level 2 — never indexed"]
        P8 -->|No| P9["CLASSIFYING<br/>language, category, title,<br/>description, tags"]
        P9 --> P10["Identity agent<br/>only for OCR'd images and scans"]
        P10 --> P11["CHUNKING<br/>1000 chars, 150 overlap,<br/>never spanning a page"]
        P11 --> P12["EMBEDDING<br/>768-d, local"]
        P12 --> P13["INDEXING<br/>old points deleted first"]
        P13 --> P14["COMPLETED"]
    end
```

**Statuses** — `documents.status` carries the coarse state; `document_processing.current_stage`
carries the fine one. Both exist because a single column cannot answer "is it done?" and "what is
it doing?" without ambiguity.

`UPLOADING → VALIDATING → DUPLICATE_CHECK → QUEUED → PROCESSING → EXTRACTING → OCR → CLASSIFYING
→ CHUNKING → EMBEDDING → INDEXING → COMPLETED`, with `FAILED`, `DUPLICATE` and `DELETED` as
terminal outcomes. Every transition goes through one function; an illegal transition raises rather
than silently correcting.

**Idempotency.** Point ids are `uuid5(namespace, "<document_id>:<chunk_index>")`, so a retry
upserts over the same ids instead of doubling the chunks. Chunks and vectors are deleted before
being rewritten, so a reprocessed document that yields fewer chunks leaves no orphans.

---

## Chat / RAG flow

```mermaid
flowchart TD
    Q["User question"] --> BFF["POST /api/chat/conversations/:id/messages<br/>Next.js route handler, 150s budget"]
    BFF --> API["POST /api/v1/chat/conversations/:id/messages"]
    API --> A1["Validate: non-empty, ≤ 4000 chars"]
    A1 --> A2["Persist the USER message<br/>before anything can fail"]
    A2 --> A3["Load last 6 turns"]
    A3 --> RDS[("Redis<br/>chat:{user}:{conversation}")]
    RDS -->|"miss"| A4["Rebuild from chat_messages<br/>and repopulate"]
    A3 --> A5["AGENT 2 — Query Planner<br/>CrewAI · 25s · no retry"]
    A5 -->|"fails"| A6["Fallback: the raw question,<br/>no filters"]
    A5 --> A7["Validate proposed filters<br/>against the live taxonomy"]
    A7 --> A8["Embed the query<br/>768-d, local"]
    A8 --> A9["Qdrant search<br/>top-K 5 · threshold 0.35<br/>must(status = COMPLETED)"]
    A9 --> A10{"Any hits?"}

    A10 -->|"No"| N1["Refusal — AGENT 3 IS NOT CALLED"]
    N1 --> N2["HTTP 200<br/>is_grounded false · no sources<br/>NO_RELEVANT_CONTEXT"]

    A10 -->|"Yes"| C1["AGENT 3 — Response Composer<br/>CrewAI · 60s · 1 retry"]
    C1 -->|"fails"| C2["Fallback: templated answer<br/>over the same passages"]
    C1 --> C3["Intersect cited ids<br/>with the ids supplied"]
    C3 --> C4["Resolve document ids<br/>against live rows"]
    C4 --> C5["Mask validated PAN / Aadhaar"]
    C5 --> C6["Persist assistant message<br/>+ message_sources"]
    C6 --> C7["Update Redis context"]
    C7 --> C8["Answer + sources + latency"]
    C8 --> UI["Rendered as Markdown<br/>with source chips"]
```

Two steps carry most of the safety:

1. **Agent 3 is not called when retrieval is empty.** Not called and told to refuse — not called.
   A model handed zero passages and asked to answer will often oblige.
2. **Citations are verified, not trusted.** `cited_chunk_ids` is intersected with the ids actually
   supplied, then again against live `documents` rows. An answer that claims to be grounded but
   cites nothing verifiable is replaced by the templated answer.

---

## AI architecture

Exactly three agents ([ADR-002](docs/architecture/decisions/ADR-002-crewai-three-agents.md)).
Adding a fourth requires a new ADR.

| #   | Agent             | Runs in                        | Framework         | Timeout | Retries | Fallback                                   |
| --- | ----------------- | ------------------------------ | ----------------- | ------: | ------: | ------------------------------------------ |
| 1   | Identity Document | Celery task, after OCR         | Direct Gemini SDK |     30s |       — | `document_type = null`, indexing continues |
| 2   | Query Planner     | Chat request, before retrieval | **CrewAI**        |     25s |       0 | Search the raw question, no filters        |
| 3   | Response Composer | Chat request, after retrieval  | **CrewAI**        |     60s |       1 | Templated answer over the same passages    |

Agent 1 stays a direct SDK call by decision: it runs on the async path where the framework buys
nothing — no delegation, no shared memory, no latency budget to defend.

**What is not an agent.** File and MIME validation, signature checks, hashing, duplicate
comparison, text extraction, OCR invocation, normalisation, language detection, chunking,
embedding, vector upsert, status transitions and all database access are ordinary services. None
may be routed through an agent.

**No agent failure produces a 500 on the chat path.** `crew.run_json` never raises, never blocks
past its timeout, and never logs a prompt (prompts contain document text).

**Answer formatting.** The composer returns Markdown and picks the shape from the question — one
sentence for a direct fact, prose for an explanation, bullets for parallel items, a numbered list
only when order matters, a table for a real comparison, a fenced block for code or configuration.
The rules are phrased as _"choose one shape that fits"_, never as a menu: a model shown a list of
available formats uses them, which is how every answer becomes bullet points.

**Model configuration.** Not an environment variable. The active provider is a row in
`llm_providers`, chosen at `/super-admin/llm-providers`, and switching it takes effect on the next
request in both the API and the worker — no restart ([ADR-010](docs/architecture/decisions/ADR-010-database-backed-llm-credentials.md)).
The model id is stored **bare** and the `provider/` prefix is composed in exactly one place;
storing the prefixed form once doubled it and broke both AI paths silently.

**Cost** (nothing is metered or cached in v1)

| Operation                   | Gemini calls |
| --------------------------- | -----------: |
| Document, non-identity      |            3 |
| Document, identity          |            4 |
| Chat message, context found |            2 |
| Chat message, nothing found |            1 |

---

## Database architecture

PostgreSQL is the permanent source of truth. Qdrant and Redis are derived and may be rebuilt.

- **ORM** — SQLAlchemy 2.0 with typed `Mapped` / `mapped_column`
- **Migrations** — Alembic. `migrations/env.py` builds its engine from `settings.DATABASE_URL`
  directly rather than through `alembic.ini`, because configparser interpolation corrupts a
  password containing `%`
- **Connection** — pooled engine, `expire_on_commit=False`
- **Primary keys** — UUID throughout
- **Deletion** — soft, via `deleted_at`, so citations in old answers still resolve

### Tables

| Table                 | Purpose                                                             |
| --------------------- | ------------------------------------------------------------------- |
| `users`               | The administrator account, bcrypt password hash                     |
| `audit_logs`          | Append-only record of every mutation; `ip_address` is `INET`        |
| `document_categories` | Configurable taxonomy, seeded with 10 categories                    |
| `documents`           | One row per uploaded file, both hashes, status, metadata            |
| `document_processing` | 1:1 with `documents` — stage, task id, error, retry count, duration |
| `document_tags`       | AI or manual tags, unique per document                              |
| `document_chunks`     | Chunk text, page number, and the mirrored Qdrant point id           |
| `conversations`       | Chat threads, denormalised `message_count` and `last_message_at`    |
| `chat_messages`       | Immutable messages; `created_at` only, no `updated_at`              |
| `message_sources`     | Citations — `document_id` is `ON DELETE RESTRICT`                   |

```mermaid
erDiagram
    users ||--o{ documents : "created_by"
    users ||--o{ conversations : "user_id"
    users ||--o{ audit_logs : "user_id"

    document_categories ||--o{ documents : "category_id"
    documents ||--|| document_processing : "1:1"
    documents ||--o{ document_tags : "document_id"
    documents ||--o{ document_chunks : "document_id"
    documents ||--o{ documents : "duplicate_of_document_id"

    conversations ||--o{ chat_messages : "conversation_id"
    chat_messages ||--o{ message_sources : "message_id"
    documents ||--o{ message_sources : "RESTRICT"
    document_chunks ||--o{ message_sources : "SET NULL"
```

`message_sources.document_id` is `RESTRICT` on purpose: documents are soft-deleted, so a citation
in a year-old answer must still resolve. It has a practical consequence — anything that truncates
`documents` must clear the chat tables first.

**Indexes that exist for a specific query**

| Index                                              | Serves                                             |
| -------------------------------------------------- | -------------------------------------------------- |
| `(status, deleted_at)` on `documents`              | The document list, the hottest query in the system |
| `created_at` on `documents`                        | Default list ordering                              |
| `(user_id, last_message_at)` on `conversations`    | The chat sidebar                                   |
| `(conversation_id, created_at)` on `chat_messages` | Loading a conversation                             |
| `vector_point_id` on `document_chunks`             | Mapping a Qdrant hit back to a row                 |

---

## Vector database

Qdrant runs as a native Windows binary bound to `127.0.0.1`.

| Property               | Value                                                                         |
| ---------------------- | ----------------------------------------------------------------------------- |
| Collection             | `documents` (`documents_test` for tests)                                      |
| Vector size            | 768                                                                           |
| Distance               | Cosine                                                                        |
| Embedding model        | `sentence-transformers/all-mpnet-base-v2`, local                              |
| Encoder window         | 384 word-pieces — **truncates silently** past it                              |
| Point id               | `uuid5(namespace, "<document_id>:<chunk_index>")` — deterministic             |
| Top-K                  | 5                                                                             |
| Score threshold        | 0.35                                                                          |
| Indexed payload fields | `document_id`, `category_slug`, `document_type`, `language`, `tags`, `status` |

**Payload** — `document_id`, `chunk_id`, `chunk_index`, `text`, `document_name`, `document_type`,
`category_slug`, `language`, `tags`, `page_number`, `section`, `status`, `created_at`. `text` and
`document_name` are duplicated from PostgreSQL so a hit can be rendered without a join.

```mermaid
flowchart LR
    subgraph IDX["Indexing"]
        T["Chunk text"] --> E1["Local encoder"] --> V1["768-d vector"]
        V1 --> D1["Delete existing points<br/>for this document"]
        D1 --> U1["Upsert with deterministic id"]
    end

    subgraph SRCH["Retrieval"]
        Q["Question"] --> E2["Local encoder"] --> V2["768-d vector"]
        V2 --> FLT["Filter: status = COMPLETED<br/>+ validated optional filters"]
        FLT --> TOP["top-K 5 above 0.35"]
        TOP --> HIT["Hits with payload"]
    end
```

Two rules the code enforces:

- **The collection is created once and never recreated**
  ([ADR-006](docs/architecture/decisions/ADR-006-qdrant-collection-lifecycle.md)).
  `recreate_collection` deletes it — at module scope that wipes the index on every reload, silently,
  because the collection still exists afterwards and merely contains nothing.
- **The `status` filter is always applied inside the query**, never as a post-filter, so a deleted,
  failed or duplicate document can never occupy one of the five slots.

---

## Redis

Redis (Memurai on Windows) is a cache and a broker. It is **never** a source of truth.

| Use                              | Key pattern                        | TTL                  |
| -------------------------------- | ---------------------------------- | -------------------- |
| Celery broker and result backend | Celery-managed                     | —                    |
| Recent chat context              | `chat:{user_id}:{conversation_id}` | 24 h                 |
| Rate-limit counters              | `ratelimit:{bucket}:{identity}`    | window length        |
| Refresh-token denylist           | `auth:denylist:{jti}`              | until natural expiry |

Two deliberately different failure stances:

- **Chat context fails open.** A miss or an outage is not an error — the last N turns are rebuilt
  from `chat_messages` and the key repopulated. The system is fully correct with Redis empty, only
  slower by one query.
- **The token denylist fails closed.** If it cannot be consulted, a refresh is refused rather than
  silently honouring a token that may have been revoked.

Rate limiting also fails open: a cache outage should not take the API down with it.

---

## Celery

| Setting                          | Value          | Reason                                                                                                 |
| -------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------ |
| Broker / backend                 | `REDIS_URL`    | Single dependency                                                                                      |
| Pool                             | `--pool=solo`  | Windows has no `fork`; prefork is unreliable there                                                     |
| `task_acks_late`                 | `True`         | A killed worker redelivers instead of losing the task                                                  |
| `worker_prefetch_multiplier`     | `1`            | Tasks run for minutes; prefetch would idle one worker while another queues                             |
| `task_track_started`             | `True`         | So a stuck task is visible as started, not merely pending                                              |
| Default queue                    | `default`      | Single queue in v1                                                                                     |
| `imports`                        | explicit tuple | A task enqueued but not imported fails with `NotRegistered` and the document waits in `QUEUED` forever |
| `soft_time_limit` / `time_limit` | 1800s / 2100s  | The soft limit raises inside the task so it can record _why_ it stopped                                |
| `autoretry_for`                  | `()` — empty   | A blanket retry re-runs a corrupt PDF three times to reach the same conclusion                         |

```mermaid
flowchart LR
    API["FastAPI upload<br/>after COMMIT"] -->|"delay()"| RD[("Redis queue")]
    RD --> W["Celery worker<br/>solo pool"]
    W --> T["documents.process"]
    T --> P["ProcessingPipeline"]
    P --> PG[("PostgreSQL<br/>status per stage")]
    P --> QD[("Qdrant<br/>upsert")]
    T -->|"RetryableStageError"| RT["Retry 10s → 20s → 40s"]
    RT -->|"3 attempts spent"| FL2["FAILED<br/>RETRY_LIMIT_EXCEEDED"]
    T -->|"SoftTimeLimitExceeded"| FL3["FAILED<br/>PROCESSING_FAILED"]
```

Retries are explicit per stage. Only transient failures — Qdrant restarting, memory pressure, a
rate limit — are retried; a corrupt file fails immediately.

**Tasks**

| Task                | Purpose                                     |
| ------------------- | ------------------------------------------- |
| `documents.process` | The full 14-stage pipeline for one document |
| `system.health`     | Liveness probe for the worker itself        |

---

## Flower

Flower exists so the queue is observable without reading Redis by hand. It attaches to the same
broker and reports:

- Registered workers, their pool and concurrency
- Active, scheduled, reserved and completed tasks
- Task arguments, runtime, result and traceback
- Success and failure counts per task

```bash
cd backend
scripts\start_flower.bat
# → http://127.0.0.1:5555
```

> **Flower has no authentication by default and it displays task arguments.** It is bound to
> `127.0.0.1` for exactly that reason. Do not bind it to `0.0.0.0` on a shared network.

---

## API overview

Base path `/api/v1`. Every endpoint requires a bearer token except those marked otherwise.

### Auth

| Method | Endpoint        | Description                                        | Auth       |
| ------ | --------------- | -------------------------------------------------- | ---------- |
| POST   | `/auth/login`   | Email + password → access token and refresh cookie | **No**     |
| POST   | `/auth/refresh` | Refresh cookie → new access token                  | **Cookie** |
| POST   | `/auth/logout`  | Revoke the refresh token                           | Yes        |
| GET    | `/auth/me`      | Current administrator                              | Yes        |

### Documents

| Method | Endpoint                          | Description                                            | Auth |
| ------ | --------------------------------- | ------------------------------------------------------ | ---- |
| POST   | `/admin/documents/upload`         | Multi-file upload, partial success                     | Yes  |
| GET    | `/admin/documents`                | List — search, filter, sort, paginate                  | Yes  |
| GET    | `/admin/documents/categories`     | Filter options: categories, statuses, types, languages | Yes  |
| GET    | `/admin/documents/{id}`           | Detail with tags, processing info, chunk count         | Yes  |
| PATCH  | `/admin/documents/{id}`           | Metadata-only edit — no re-embedding                   | Yes  |
| DELETE | `/admin/documents/{id}`           | Soft delete plus vector removal                        | Yes  |
| POST   | `/admin/documents/{id}/reprocess` | Re-run the pipeline — `202`                            | Yes  |
| GET    | `/admin/documents/{id}/status`    | Lightweight polling target                             | Yes  |
| GET    | `/admin/documents/{id}/download`  | Stream the original as an attachment                   | Yes  |

**List parameters** — `search`, `status` (repeatable), `category_id` (repeatable),
`document_type`, `language`, `tag`, `created_from`, `created_to`, `sort`
(`created_at` · `file_name` · `file_size` · `status`), `order`, `page`, `page_size`,
`include_deleted`. `sort` is whitelisted, not interpolated: a column name from a query string is
an injection vector even when values are bound.

### Chat

| Method | Endpoint                            | Description                                       | Auth |
| ------ | ----------------------------------- | ------------------------------------------------- | ---- |
| POST   | `/chat/conversations`               | Create (lazy — not listed until it has a message) | Yes  |
| GET    | `/chat/conversations`               | List, most recent first, empties excluded         | Yes  |
| GET    | `/chat/conversations/{id}`          | Detail with messages and citations                | Yes  |
| PATCH  | `/chat/conversations/{id}`          | Rename                                            | Yes  |
| DELETE | `/chat/conversations/{id}`          | Soft delete; messages and citations survive       | Yes  |
| POST   | `/chat/conversations/{id}/messages` | Ask — answer plus sources                         | Yes  |

### System

| Method | Endpoint                | Description                                      | Auth   |
| ------ | ----------------------- | ------------------------------------------------ | ------ |
| GET    | `/health`               | Redis, Qdrant and agent status                   | **No** |
| GET    | `/config/upload-limits` | Server limits, so client validation cannot drift | Yes    |
| GET    | `/docs`, `/redoc`       | Interactive docs — **disabled in production**    | **No** |

### Response envelope

```json
{ "success": true, "data": {}, "message": "optional" }
```

```json
{
  "success": false,
  "error_code": "DOCUMENT_NOT_FOUND",
  "message": "…",
  "details": {},
  "request_id": "…"
}
```

44 machine-readable error codes are defined; see
[docs/api/error-codes.md](docs/api/error-codes.md).

### Rate limits

| Scope                          | Limit        | Keyed by                                          |
| ------------------------------ | ------------ | ------------------------------------------------- |
| `POST /auth/login`             | 5 / minute   | **IP** — the account field is attacker-controlled |
| `POST /admin/documents/upload` | 20 / minute  | User                                              |
| `POST /chat/.../messages`      | 30 / minute  | User                                              |
| Everything else                | 300 / minute | User                                              |

`X-RateLimit-Limit` and `X-RateLimit-Remaining` are returned on every response; `429` carries
`Retry-After`.

---

## Project structure

```text
EnterpriseRAG/
├── backend/
│   ├── app/
│   │   ├── main.py                  FastAPI factory, middleware order, /health
│   │   ├── core/                    config-adjacent primitives
│   │   │   ├── database.py          engine, session, declarative Base
│   │   │   ├── security.py          bcrypt, JWT encode/decode
│   │   │   ├── logging.py           JSON formatter + PII redaction filter
│   │   │   ├── rate_limit.py        Redis fixed-window limiter
│   │   │   ├── error_codes.py       44 machine-readable codes
│   │   │   ├── exceptions.py        AppError hierarchy with status + code
│   │   │   └── dependencies.py      current_user, request_id
│   │   ├── middlewares/             security headers, request id, auth, rate limit
│   │   ├── modules/                 one package per bounded context
│   │   │   ├── auth/                controllers · models · repositories · routes · schemas · services
│   │   │   ├── documents/           upload, pipeline, management, state machine
│   │   │   ├── chat/                conversations, retrieval, chat service
│   │   │   └── ai/                  crew, query_planner, response_composer,
│   │   │                            enrichment, identity_agent, gemini_client
│   │   ├── vector/                  embeddings · client · repository
│   │   ├── storage/                 StorageService ABC + local backend
│   │   ├── cache/                   Redis client and token denylist
│   │   ├── utils/                   file_validation · hashing · pii
│   │   └── workers/                 celery_app + tasks/
│   ├── config/                      settings.py · taxonomy.py
│   ├── migrations/                  Alembic versions
│   ├── scripts/                     start_*.bat · create_admin · seed_taxonomy
│   ├── tests/                       12 suites
│   ├── storage/                     uploaded blobs — gitignored
│   ├── logs/                        JSON logs — gitignored
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── (auth)/login/        login screen
│   │   │   ├── (dashboard)/         dashboard · documents · upload · chat/[id]
│   │   │   └── api/                 BFF route handlers — the only place a token is used
│   │   ├── components/
│   │   │   ├── ui/                  Button · Card · StatusBadge
│   │   │   ├── layout/              Shell · Sidebar
│   │   │   ├── documents/           UploadPanel · DocumentTable · FilterBar · DocumentDrawer
│   │   │   └── chat/                ChatPanel · ConversationList · Markdown · ChatShell
│   │   ├── lib/                     session (cookies) · api (server fetch) · validation
│   │   └── types/                   api.ts — response contracts
│   ├── vitest.config.mts
│   └── package.json
│
├── docs/                            32 files — architecture, ADRs, features, security, testing
├── stitch_documind_ai_interface/    original design exports
└── README.md
```

### Backend layering

`routes → controllers → services → repositories → models`. A route defines a path and wires
dependencies; a controller translates HTTP into service calls; a service holds business rules; a
repository holds database access and no rules.

---

## Application screenshots

**No screenshots are committed yet.** The application runs, so these can be captured — they are
listed here as the set to produce, and this section should be updated with real images rather than
left with placeholders.

Suggested location: `docs/screenshots/`.

| Screen                 | Route                      | File                  | What it shows                                  |
| ---------------------- | -------------------------- | --------------------- | ---------------------------------------------- |
| Login                  | `/login`                   | `login.png`           | Email and password, timing-safe failure        |
| Dashboard              | `/dashboard`               | `dashboard.png`       | Four stat cards and recent documents           |
| Upload                 | `/upload`                  | `upload.png`          | Drag-and-drop, per-file validation and results |
| Documents              | `/documents`               | `documents.png`       | Table with search, filters, sortable headers   |
| Document detail        | `/documents` (drawer)      | `document-detail.png` | Metadata edit, download, reprocess, delete     |
| Chat — empty           | `/chat`                    | `chat-empty.png`      | "How can I help you today?"                    |
| Chat — grounded answer | `/chat/[id]`               | `chat-answer.png`     | Formatted answer with source chips             |
| Chat — refusal         | `/chat/[id]`               | `chat-refusal.png`    | Refusal styled as an answer, no chips          |
| Flower                 | `127.0.0.1:5555`           | `flower.png`          | Worker and task monitoring                     |
| Qdrant                 | `127.0.0.1:6333/dashboard` | `qdrant.png`          | Collection and point count                     |

---

## Installation

### Prerequisites

| Requirement | Version               | Notes                                                  |
| ----------- | --------------------- | ------------------------------------------------------ |
| Python      | 3.11                  | 3.12+ untested                                         |
| Node.js     | ≥ 20.12 recommended   | 20.9 works; some tooling wants newer                   |
| PostgreSQL  | 18.x                  | Any 14+ should work                                    |
| Redis       | Memurai on Windows    | **Memurai Developer Edition prohibits production use** |
| Qdrant      | 1.19 Windows binary   | Not a pip package                                      |
| Tesseract   | 5.5 UB-Mannheim build | Not a pip package                                      |

### 1. PostgreSQL

```sql
CREATE DATABASE enterprise_rag;
CREATE DATABASE enterprise_rag_test;
```

### 2. Redis / Memurai

Install Memurai and confirm it is listening on `127.0.0.1:6379`.

### 3. Qdrant

Download `qdrant-x86_64-pc-windows-msvc.zip`, extract to `C:\qdrant`, and bind it to loopback in
`C:\qdrant\config\config.yaml`:

```yaml
service:
  host: 127.0.0.1
  http_port: 6333
```

### 4. Tesseract

Install the UB-Mannheim build and tick _Add to PATH_. If it is not on `PATH`, set
`TESSERACT_CMD` to the full binary path — that is more robust in any case.

Verify from the application's own point of view, not just the shell:

```bash
py -c "from app.modules.documents.services.ocr_service import tesseract_available; print(tesseract_available())"
```

### 5. Backend

```bash
cd backend
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then fill it in
alembic upgrade head
py scripts\seed_taxonomy.py
py scripts\create_admin.py  # prompts for email and password
```

> `pip install` takes 30–45 minutes on a cold cache. Most of it is CrewAI's dependency tree
> (chromadb, lancedb, onnxruntime, pyarrow, torch). It is not stuck.

The embedding model (~420 MB) downloads on first use. To pre-warm:

```bash
py -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-mpnet-base-v2')"
```

### 6. Frontend

```bash
cd frontend
npm install
copy .env.example .env.local
```

---

## Environment configuration

`backend/.env` — never committed; `.gitignore` covers `.env`, `.env.*` and `*.env`.

```env
# Application
APP_NAME=EnterpriseRAG
ENVIRONMENT=development          # development | production — production disables /docs
DEBUG=true
API_V1_PREFIX=/api/v1

# PostgreSQL
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/enterprise_rag
DB_ECHO=false
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10

# Redis / Memurai
REDIS_URL=redis://localhost:6379/0

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=                  # set if Qdrant is bound beyond loopback
QDRANT_COLLECTION=documents

# Auth
JWT_SECRET=                      # 32+ characters, validated at startup
JWT_ALGORITHM=HS256
ACCESS_TOKEN_MINUTES=30
REFRESH_TOKEN_DAYS=7
BCRYPT_ROUNDS=12
CORS_ORIGINS=http://localhost:3000   # never "*"

# Embeddings
EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
EMBEDDING_DIM=768

# OCR
TESSERACT_CMD=                   # full path if not on PATH
OCR_LANGUAGE=eng
OCR_MIN_CHARS_PER_PAGE=50        # below this, a page is treated as needing OCR

# AI — the provider is a database row, not a variable. Register one at
# /super-admin/llm-providers. Upgrading? See "Credential encryption" below.
LLM_CONFIG_STALENESS_SECONDS=300 # how long a cached config may answer if the DB is unreadable

# Credential encryption — required before any provider can be stored
ENCRYPTION_KEYS=1:<random>       # "<id>:<secret>", comma-separated, >= 32 chars each
ENCRYPTION_ACTIVE_KEY_ID=1       # new credentials use this id; old ones decrypt under theirs

# Storage
STORAGE_BACKEND=local
STORAGE_ROOT=                    # defaults to backend/storage

# Upload limits
MAX_DOCUMENT_SIZE_MB=20
MAX_IMAGE_SIZE_MB=2
MAX_FILES_PER_BATCH=20

# Chunking and retrieval
CHUNK_SIZE_CHARS=1000
CHUNK_OVERLAP_CHARS=150
RETRIEVAL_TOP_K=5
RETRIEVAL_MIN_SCORE=0.35

# Agents
AGENT_PLAN_TIMEOUT_SECONDS=25
AGENT_COMPOSE_TIMEOUT_SECONDS=60
AGENT_PLAN_RETRIES=0
AGENT_COMPOSE_RETRIES=1

# Chat
CHAT_CONTEXT_TURNS=6
CHAT_CONTEXT_TTL_SECONDS=86400
MAX_MESSAGE_CHARS=4000

# Document management
DEFAULT_PAGE_SIZE=25
MAX_PAGE_SIZE=100
DELETE_SOURCE_FILE_ON_DELETE=false   # keeping the blob makes a soft delete recoverable

# Rate limits — "<count>/<window>"; a bare count is rejected at startup
RATE_LIMIT_LOGIN=5/minute
RATE_LIMIT_UPLOAD=20/minute
RATE_LIMIT_CHAT=30/minute
RATE_LIMIT_DEFAULT=300/minute

# Logging
LOG_LEVEL=INFO
LOG_JSON=true
```

`frontend/.env.local` — one variable, server-side only:

```env
BACKEND_URL=http://localhost:8000
```

> **Never prefix a secret with `NEXT_PUBLIC_`.** Next.js inlines those into the browser bundle at
> build time, which publishes the value. `tests/test_bundle_secrets.py` scans the built output for
> secret-shaped strings.

---

## Running the application

Five processes, in this order. Each `.bat` activates the venv itself.

| #   | Service             | Command                            | Address          |
| --- | ------------------- | ---------------------------------- | ---------------- |
| 1   | PostgreSQL          | Windows service                    | `localhost:5432` |
| 2   | Redis / Memurai     | Windows service                    | `127.0.0.1:6379` |
| 3   | Qdrant              | `backend\scripts\start_qdrant.bat` | `127.0.0.1:6333` |
| 4   | FastAPI             | `backend\scripts\start_api.bat`    | `127.0.0.1:8000` |
| 5   | Celery worker       | `backend\scripts\start_worker.bat` | —                |
| 6   | Flower _(optional)_ | `backend\scripts\start_flower.bat` | `127.0.0.1:5555` |
| 7   | Frontend            | `cd frontend && npm run dev`       | `localhost:3000` |

```mermaid
flowchart LR
    PG["1 PostgreSQL"] --> RD["2 Redis"] --> QD["3 Qdrant"] --> API["4 FastAPI"] --> W["5 Celery worker"] --> FE["7 Frontend"]
    W -.-> FL["6 Flower"]
```

**The worker is not optional.** Without it, uploads succeed and then sit in `QUEUED` forever —
the API enqueues, the worker processes.

Verify the whole stack in one call:

```bash
curl http://localhost:8000/health
```

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "redis": "up",
    "qdrant": "up",
    "agents": {
      "crewai_installed": true,
      "agents_enabled": true
    },
    "llm": {
      "provider": "gemini",
      "model": "gemini-2.0-flash",
      "resolved_model_id": "gemini/gemini-2.0-flash",
      "key_fingerprint": "1b677fc183c308d3",
      "config_version": 12,
      "config_confirmed_age_s": 0.4,
      "degraded": false,
      "configured": true,
      "encryption_configured": true
    }
  }
}
```

`agents_enabled` means _configured_, not _working_. `crew.probe()` makes one real call when that
distinction matters.

The `llm` block is what the process is **actually answering with**, which is not always what the
database says: a switch that was never picked up would show here as an old `config_version`. Both
model ids are reported because they differ, and a mismatch between them is invisible otherwise —
a doubled prefix once made every agent call fail while this endpoint reported the agents enabled.

`configured: false` means no provider is active, and every answer is coming from the templated
fallback. `degraded: true` means the configuration could not be re-confirmed and the cached one is
still being served, which is allowed for `LLM_CONFIG_STALENESS_SECONDS` and then stops.

---

## Testing

```bash
cd backend  && py -m pytest tests -q      # ~15 min — needs PostgreSQL, Redis and Qdrant
cd frontend && npm test                   # ~20 s
cd frontend && npm run build              # type-checks everything; treat as a gate
```

Backend: **354 tests** across 13 suites — 337 run by default, 17 opt-in.
Frontend: **58 tests**.

| Suite                             | Tests | Covers                                                              |
| --------------------------------- | ----: | ------------------------------------------------------------------- |
| `test_document_management_api.py` |    60 | List, filters, sort whitelist, edit, delete, reprocess, download    |
| `test_security.py`                |    44 | Hashing, JWT, PII redaction and display masking, rate-limit parsing |
| `test_pipeline_units.py`          |    44 | State machine, chunking, Verhoeff, JSON salvage                     |
| `test_upload_validation.py`       |    42 | Signatures, sizes, filenames, extensions                            |
| `test_chat_units.py`              |    31 | Citation verification, format rules, planner fallbacks              |
| `test_chat_api.py`                |    31 | Grounding, refusal, PII masking, context rebuild                    |
| `test_upload_api.py`              |    24 | Upload endpoint against a real database                             |
| `test_pipeline_e2e.py`            |    18 | The real pipeline end to end, including OCR                         |
| `test_security_api.py`            |    17 | **Route enumeration**, headers, rate limits, audit content          |
| `test_ocr.py`                     |    11 | Live Tesseract — images, scanned PDFs, page mapping                 |
| `test_auth_routes.py`             |    10 | Login, refresh, logout, `/me` against a real database               |
| `test_bundle_secrets.py`          |     5 | No secret in the built frontend bundle                              |
| `test_grounding_eval.py`          |    17 | **Opt-in** — 10 answerable + 5 unanswerable questions               |

The test database is `enterprise_rag_test` and the collection is `documents_test`, never the
development ones.

> **Run one suite at a time.** Every suite truncates the shared test database between tests, so two
> pytest processes at once delete each other's rows and produce failures unrelated to the code.

The grounding evaluation costs real Gemini calls and is therefore explicit:

```bash
set RUN_GROUNDING_EVAL=1
py -m pytest tests/test_grounding_eval.py -v
```

It skips itself when no provider is active — without one every answer comes from the
templated fallback, which is grounded by construction, so the suite would pass while proving
nothing.

---

## Error handling

### Upload — per-file, inside a `200`

A whole-request failure is an error; a per-file failure is data. A batch of 20 where 3 are invalid
returns `200` with 17 queued and 3 rejected, each with its own code.

| Situation                     | Code                        | Result                               |
| ----------------------------- | --------------------------- | ------------------------------------ |
| `.doc` / `.ppt`               | `LEGACY_FORMAT_UNSUPPORTED` | Rejected with a "save as .docx" hint |
| Unknown extension             | `INVALID_FILE_TYPE`         | Rejected                             |
| Bytes disagree with extension | `FILE_SIGNATURE_MISMATCH`   | Rejected                             |
| Over the class limit          | `FILE_TOO_LARGE`            | Rejected                             |
| Zero bytes                    | `FILE_EMPTY`                | Rejected                             |
| Same bytes already live       | `DUPLICATE_DOCUMENT`        | Nothing stored                       |
| Same bytes twice in one batch | `DUPLICATE_IN_BATCH`        | First kept                           |
| More than 20 files            | `TOO_MANY_FILES`            | **400** — a request-level error      |

### Processing — recorded on the document, never returned to a caller

| Situation                            | Code                                          | Behaviour                                                                   |
| ------------------------------------ | --------------------------------------------- | --------------------------------------------------------------------------- |
| Blob missing at execution            | `STORAGE_FILE_MISSING`                        | `FAILED`                                                                    |
| Parse failure                        | `EXTRACTION_FAILED`                           | `FAILED`                                                                    |
| Tesseract absent                     | `OCR_UNAVAILABLE`                             | `FAILED` — never silently empty text                                        |
| OCR error                            | `OCR_FAILED`                                  | `FAILED`                                                                    |
| No text at all                       | `NO_TEXT_EXTRACTED`                           | `FAILED`                                                                    |
| Same content as a completed document | `CONTENT_DUPLICATE`                           | `DUPLICATE`, never indexed                                                  |
| Embedding or indexing failure        | `EMBEDDING_FAILED` / `VECTOR_INDEXING_FAILED` | Retried 10s → 20s → 40s                                                     |
| Three retries spent                  | `RETRY_LIMIT_EXCEEDED`                        | `FAILED`                                                                    |
| Over 30 minutes                      | `PROCESSING_FAILED`                           | `FAILED` with the reason recorded                                           |
| Classification failure               | —                                             | **Degrades** — title falls back to the filename, the document still indexes |

Empty OCR output fails deliberately rather than indexing nothing: it is indistinguishable from a
blank page and would poison the content hash, matching every future blank page.

### Chat

| Situation                      | Response                                              |
| ------------------------------ | ----------------------------------------------------- |
| Empty message                  | `400 MESSAGE_EMPTY`                                   |
| Over 4000 characters           | `400 MESSAGE_TOO_LONG`                                |
| Qdrant unreachable             | `503 SEARCH_FAILED` — the question is still persisted |
| Agent 2 fails                  | Silent — retrieve on the raw question                 |
| Agent 3 fails                  | Silent — templated grounded answer                    |
| Nothing above threshold        | **`200`** with `is_grounded: false`                   |
| Model cites nothing verifiable | Templated answer substituted                          |
| Unknown conversation           | `404 CONVERSATION_NOT_FOUND`                          |

### Management

`DOCUMENT_NOT_FOUND` 404 · `DOCUMENT_ALREADY_DELETED` 409 · `DOCUMENT_NOT_EDITABLE` 409 (any
non-terminal status, including `QUEUED`, because a worker can claim the row a millisecond later) ·
`DOCUMENT_NOT_REPROCESSABLE` 409 · `RATE_LIMIT_EXCEEDED` 429.

---

## Security

| Area                   | Implementation                                                                                                                                           |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Authentication         | JWT bearer, 30-minute access token; refresh in an httpOnly cookie                                                                                        |
| Token revocation       | Redis denylist by `jti`, **fails closed**                                                                                                                |
| Password storage       | bcrypt, 12 rounds                                                                                                                                        |
| Login enumeration      | Unknown email and wrong password are identical in body, status and timing                                                                                |
| Authorisation          | Single admin role; conversations are scoped by `user_id` anyway                                                                                          |
| Browser token exposure | **None.** The browser only ever holds an httpOnly cookie set by the BFF                                                                                  |
| Public paths           | An **exact-match** frozenset, never a prefix test                                                                                                        |
| Route coverage         | A test enumerates the app's own routes and asserts 401 without a token                                                                                   |
| Rate limiting          | Redis fixed window; login per-IP, the rest per-user                                                                                                      |
| Security headers       | `nosniff`, `X-Frame-Options: DENY`, CSP, `Referrer-Policy`, `Permissions-Policy`; HSTS in production only                                                |
| CORS                   | Explicit origin list, never `*`                                                                                                                          |
| File validation        | Extension + MIME + magic bytes + size; the client is UX only                                                                                             |
| Path traversal         | Storage keys are generated; the client never supplies a path                                                                                             |
| Download safety        | `Content-Disposition: attachment` with a re-sanitised filename, plus `nosniff`                                                                           |
| SQL injection          | Parameterised throughout; `sort` and filters whitelisted                                                                                                 |
| Prompt injection       | The composer is told context is reference material, never instruction. Reduced, not eliminated — the real mitigation is that the corpus is admin-curated |
| Markdown rendering     | `rehype-raw` deliberately unused, so raw HTML is escaped; link hrefs restricted to http/https/mailto                                                     |
| PII in logs            | A logging **filter** redacts Aadhaar and PAN in every sink, including tracebacks                                                                         |
| PII on screen          | Validated PAN/Aadhaar masked in chat answers before they are stored or returned                                                                          |
| Audit trail            | Append-only, with `user_id`, `request_id`, IP and user agent                                                                                             |
| Secrets                | Backend `.env` only; a test scans the built frontend bundle                                                                                              |
| Interactive docs       | Disabled when `ENVIRONMENT=production`                                                                                                                   |

**Why the exact-match public path set matters.** A `startswith()` exemption written for one path
also exempts every route sharing that prefix. That exact bug exists in a sibling project in this
codebase and left ten endpoints — including upload — reachable with no token. The route
enumeration test exists to make that class of mistake impossible to reintroduce quietly.

### Operational exposure

| Service               | Rule                                                                     |
| --------------------- | ------------------------------------------------------------------------ |
| **Flower :5555**      | No auth by default; displays task arguments. Bind to `127.0.0.1`         |
| **Qdrant :6333**      | Dashboard has no auth by default. Loopback only, or set `QDRANT_API_KEY` |
| **Memurai :6379**     | No password by default. Loopback only                                    |
| **PostgreSQL :5432**  | Not exposed beyond localhost                                             |
| **`/docs`, `/redoc`** | Disabled in production                                                   |

Three of these are unauthenticated by default and each exposes real data. Binding any of them to
`0.0.0.0` on a shared network is the single easiest mistake to make here.

---

## Troubleshooting

| Symptom                                        | Cause                                                                               | Fix                                                                                        |
| ---------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Uploads stay `QUEUED` forever                  | The Celery worker is not running, or the task module is not in `celery_app.imports` | Start the worker; check for `NotRegistered` in its log                                     |
| `Could not reach the server` in chat           | FastAPI is not running on `BACKEND_URL`                                             | Start the API. The BFF now distinguishes a refused connection from a timeout               |
| `The server took longer than 150s`             | The two agents plus retrieval exceeded the frontend budget                          | Check the API log; the answer may have been produced and persisted                         |
| Every answer looks like a list of raw excerpts | Agents are failing, so the templated fallback is in use                             | `curl /health` and check `agents_enabled`; then `crew.probe()`                             |
| Agents fail with a valid key                   | The stored model id carried a provider prefix                                       | The prefix is stripped on save and again when composed. No restart needed                  |
| `LLM_NO_ACTIVE_PROVIDER` on every chat        | No provider row is active                                                           | Register and activate one at `/super-admin/llm-providers`                                  |
| `LLM_CONFIG_UNAVAILABLE`                      | The database is unreadable, or the credential cannot be decrypted                   | Check `/health`; confirm `ENCRYPTION_KEYS` still holds the key id the row names            |
| `OCR_UNAVAILABLE` on every image               | Tesseract not installed or not on `PATH`                                            | Install it, or set `TESSERACT_CMD` to the binary                                           |
| `alembic` fails parsing `CORS_ORIGINS`         | pydantic-settings JSON-decodes complex types before validators                      | Already handled with `NoDecode`; keep the annotation                                       |
| `alembic` fails on a `%` in the password       | configparser interpolation                                                          | `migrations/env.py` builds the engine directly; do not route the URL through `alembic.ini` |
| Chat answers nothing relevant                  | Empty corpus, or every hit is below 0.35                                            | Check the document list for `COMPLETED` rows; a weak answer is deliberately withheld       |
| Search returns nothing after a restart         | `recreate_collection` used somewhere                                                | It must never be called outside tests (ADR-006)                                            |
| Tests fail with `RestrictViolation`            | A suite deleted `documents` before the chat tables                                  | Use `reset_database()` from `tests/conftest.py`                                            |
| Random test failures across suites             | Two pytest processes on the same test database                                      | Run one at a time                                                                          |
| Frontend `styleText` / `ERR_REQUIRE_ESM`       | Node 20.9 with newer tooling                                                        | `jsdom@25`, and no `@vitejs/plugin-react`                                                  |

---

## Known limitations

1. **Memurai Developer Edition prohibits production use.** A production deployment needs a
   different Redis.
2. **No virus scanning on upload.** Accepted with one trusted administrator; add ClamAV if that
   changes.
3. **Chunk text is stored in plaintext**, including identity-card contents. No encryption at rest.
4. **No redaction before sending text to Gemini.** Accepted and recorded.
5. **Prompt injection is reduced, not solved.** The corpus being admin-curated is the real
   mitigation.
6. **The encoder truncates silently past 384 word-pieces.** Chunk sizing keeps passages under it,
   but nothing raises if a chunk is oversized.
7. **`all-mpnet-base-v2` is English-centric.** Retrieval quality drops for other languages.
8. **No reranking.** The 0.35 threshold plus top-K is the whole relevance strategy.
9. **Fixed-window rate limiting** allows up to 2× the limit across a window boundary.
10. **Rate limiting and chat context fail open.** A Redis outage removes both protections rather
    than failing the request.
11. **No answer streaming.** Answers arrive whole after tens of seconds.
12. **No Playwright E2E.** The click-through — log in, upload, index, ask, follow a citation — is
    the one path no automated suite covers.
13. **Coverage is not gated.** `pytest-cov` is installed; no run enforces a floor.
14. **CrewAI brings ~90 unused transitive packages** (chromadb, lancedb, onnxruntime, pyarrow,
    kubernetes, openai). Retrieval is Qdrant and embeddings are local; none of them are used.
15. **Two Qdrant tests have failed intermittently**, only in runs whose wall-clock spanned a
    machine sleep. Unreproduced; recorded in
    [docs/implementation-status.md](docs/implementation-status.md) rather than patched blindly.

---

## Future improvements

- Answer streaming — the response contract is already a complete snapshot, so this is additive
- Persist and mask extracted identity fields, with an audited reveal action
- Reranking, and a measured revisit of `RETRIEVAL_MIN_SCORE`
- Playwright E2E across the full click-through
- Coverage gating in CI
- Response caching for repeated questions — every message currently costs two Gemini calls
- Encryption at rest for `document_chunks.text`
- A production Redis and a licensed alternative to Memurai Developer Edition

---

## Documentation

`docs/` holds the full design record — the reasoning behind the decisions this README summarises.

| Area                         | Entry point                                                                                                                       |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **Index and current status** | [docs/README.md](docs/README.md) · [docs/implementation-status.md](docs/implementation-status.md)                                 |
| **Decisions (8 ADRs)**       | [docs/architecture/decisions/](docs/architecture/decisions/)                                                                      |
| Architecture                 | [system-overview.md](docs/architecture/system-overview.md) · [backend-architecture.md](docs/architecture/backend-architecture.md) |
| Database                     | [schema.md](docs/database/schema.md)                                                                                              |
| API                          | [overview.md](docs/api/overview.md) · [error-codes.md](docs/api/error-codes.md)                                                   |
| Features                     | [docs/features/](docs/features/) — upload, duplicates, processing, management, chat, conversations                                |
| AI                           | [agents.md](docs/ai/agents.md)                                                                                                    |
| Qdrant                       | [collections.md](docs/qdrant/collections.md)                                                                                      |
| Celery                       | [tasks.md](docs/celery/tasks.md) · [state-machine.md](docs/celery/state-machine.md)                                               |
| Security                     | [security-model.md](docs/security/security-model.md) · [pii-handling.md](docs/security/pii-handling.md)                           |
| Frontend                     | [design-system.md](docs/frontend/design-system.md) · [screens.md](docs/frontend/screens.md)                                       |
| Testing                      | [test-plan.md](docs/testing/test-plan.md)                                                                                         |
| Deployment                   | [local-setup.md](docs/deployment/local-setup.md)                                                                                  |

The eight ADRs are the load-bearing artefacts: single admin role, three CrewAI agents, local
embeddings, conditional Tesseract OCR, the two-moment duplicate split, create-once collection
lifecycle, native Windows services, and the design-system resolution.
