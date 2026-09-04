# Backend Architecture

FastAPI with a modular (HMVC-style) layout. Each business module is self-contained and owns its
own routes, controllers, services, repositories, models and schemas.

## Structure

```
backend/
├── app/
│   ├── core/                 config, security, logging, exceptions, dependencies
│   ├── middlewares/          request-id, auth, error handler, rate limit
│   │
│   ├── modules/
│   │   ├── auth/             login, refresh, logout, me
│   │   ├── documents/        upload, list, detail, status, edit, delete, reprocess
│   │   ├── chat/             conversations, messages, retrieval
│   │   └── ai/               CrewAI agents, Gemini client, prompts
│   │
│   ├── workers/
│   │   ├── celery_app.py
│   │   └── tasks/            process_document.py, cleanup.py
│   │
│   ├── vector/               client, collections, embeddings, repositories, services
│   ├── cache/                Redis client, chat-context store
│   ├── storage/              storage service abstraction (§45)
│   ├── utils/                hashing, text normalisation, file signatures
│   └── main.py
│
├── config/                   settings, taxonomy
├── migrations/               Alembic
├── scripts/                  create_admin.py, reindex.py, *.bat launchers
├── storage/                  local blob root (gitignored)
├── logs/                     structured logs (gitignored)
├── tests/
└── requirements.txt
```

Each module follows the same internal shape:

```
module/
├── controllers/    HTTP ⇄ service translation
├── models/         SQLAlchemy
├── repositories/   database access only
├── routes/         path definitions + DI wiring
├── schemas/        Pydantic contracts
└── services/       business logic
```

## Layer rules (§12)

| Layer | May do | Must never |
| ----- | ------ | ---------- |
| **Routes** | Declare paths, wire dependencies, set response models | Contain business logic or touch the database |
| **Controllers** | Translate request → service call → response | Build SQL, call external APIs directly |
| **Services** | Business rules, orchestration, calls to AI/vector/storage | Import FastAPI, know about HTTP |
| **Repositories** | Queries and persistence | Contain business rules |
| **Models** | Table definitions and relationships | Contain validation logic |
| **Schemas** | Request/response shapes and field validation | Reach the database |

The important consequence: **a service can be called from a Celery task as easily as from a
route**, because it knows nothing about HTTP. The document-processing pipeline depends on this —
the same services run inside the worker.

## Request lifecycle

```
Request
  → RequestIDMiddleware        attach request_id, start timer
  → CORSMiddleware
  → RateLimitMiddleware        selected routes only (§40)
  → AuthMiddleware             validate JWT, load user, attach to state
  → Router → Controller → Service → Repository
  → Response
  → ErrorHandler               map exceptions to {error_code, message}
  → structured log line        request_id, user_id, path, status, duration
```

## Authentication

JWT bearer, validated by middleware. Single admin role
([ADR-001](decisions/ADR-001-single-admin-role.md)), so authorisation is a binary
authenticated/not check — there is no permission matrix to evaluate.

**Public paths** (the only ones): `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`,
`GET /health`, and — in non-production only — `/docs`, `/redoc`, `/openapi.json`.

`/auth/refresh` is public by necessity: it is called precisely when the access token has expired,
and it authenticates with the `httpOnly` refresh cookie plus the Redis denylist instead.

Everything else requires a valid token.

> ⚠️ **Path-prefix exemptions are a known trap.** A middleware that skips auth using
> `path.startswith(...)` will also exempt any router sharing that prefix. Exemptions must be an
> **exact-match set**, never a prefix test. This exact bug was found in a sibling project in this
> codebase and exposed ten endpoints. See [security/security-model.md](../security/security-model.md).

## Error handling (§38)

One envelope for every failure, and no stack traces ever reach a client.

```json
{
  "success": false,
  "error_code": "FILE_TOO_LARGE",
  "message": "File exceeds the 20 MB limit for documents.",
  "details": { "file_name": "scan.pdf", "size_bytes": 25690112, "limit_bytes": 20971520 },
  "request_id": "01J8…"
}
```

HTTP status codes are used correctly — `400`, `401`, `404`, `409`, `413`, `422`, `429`, `500` —
and carry meaning. The status and `error_code` always agree.

> This is a deliberate departure from a pattern seen elsewhere in this codebase, where errors
> returned HTTP 200 with a `Success: false` body. That makes every monitoring check and every
> status-based test pass while the API is broken. Not repeated here.

Full list: [api/error-codes.md](../api/error-codes.md).

## Configuration (§45, §50)

All settings come from one Pydantic `Settings` object loaded from `.env`. No module reads
`os.getenv` directly, and no business limit is written in more than one place.

```python
MAX_DOCUMENT_SIZE_MB = 20
MAX_IMAGE_SIZE_MB = 2
ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "docx", "pptx", "txt"}
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
CHUNK_SIZE_CHARS = 1000
CHUNK_OVERLAP_CHARS = 150
RETRIEVAL_TOP_K = 5
SEMANTIC_DUPLICATE_ENABLED = False
SEMANTIC_DUPLICATE_THRESHOLD = 0.95
OCR_MIN_CHARS_PER_PAGE = 50
```

The same constants are exposed to the frontend through a public config endpoint so client-side
validation cannot drift from server-side validation.

## Storage abstraction (§45)

`StorageService` is an interface with `save`, `open`, `delete` and `exists`, taking an opaque
`storage_key` — never an absolute path. `LocalStorageService` is the only implementation in v1;
S3 or Azure Blob can be added without touching document business logic.

No `C:\...` path appears anywhere outside configuration.

## Logging (§39)

Structured JSON to `logs/`, with `request_id`, `user_id`, `document_id`, `task_id`,
`conversation_id`, `stage`, `duration_ms`, `status`.

Never logged: passwords, tokens, API keys, document contents, or extracted PII. Identity-document
fields from agent 1 are masked at the logger — see
[security/pii-handling.md](../security/pii-handling.md).
