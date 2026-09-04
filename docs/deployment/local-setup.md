# Local Setup — Windows

Native services, no Docker
([ADR-007](../architecture/decisions/ADR-007-native-windows-services.md)).

Verified on the target host during Phase 0: Windows 10 Pro · Python **3.11.0** (as `py`) ·
Node **20.9.0** · npm 10.1.0 · PostgreSQL **already running on 5432** · Git 2.42.

## Installs

### 1. PostgreSQL — already running ✅

Port 5432 is listening. Create the databases:

```sql
CREATE DATABASE enterprise_rag;
CREATE DATABASE enterprise_rag_test;
```

`psql` is not on `PATH`; use pgAdmin, or add PostgreSQL's `bin` directory.

### 2. Memurai (Redis for Windows)

Download the free **Developer Edition** from <https://www.memurai.com/get-memurai>. It installs
as a Windows service on 6379.

> 🔴 **Developer Edition prohibits production use**, requires a restart every 10 days, caps at
> 50% of system memory and allows 10 client IPs. Fine for development. Production needs Linux,
> WSL2, or an Enterprise licence.

Verify: `memurai-cli ping` → `PONG`.

### 3. Qdrant

Download `qdrant-x86_64-pc-windows-msvc.zip` from
<https://github.com/qdrant/qdrant/releases> (v1.19.0 or later). Extract `qdrant.exe` into
`C:\qdrant\`.

It creates `./storage` beside itself on first run. Verify at
<http://localhost:6333/dashboard>.

### 4. Tesseract

Install the UB-Mannheim build from
<https://github.com/UB-Mannheim/tesseract/wiki>, adding it to `PATH` during setup.

Verify: `tesseract --version`. If it is not on `PATH`, set `TESSERACT_CMD` in `.env` to the
full path of the binary — the installer's "Add to PATH" box does not always take effect for an
already-open shell, and pointing at the binary directly is more robust regardless:

```
TESSERACT_CMD=C:\Program Files\Tesseract-OCR	esseract.exe
```

Confirm the application agrees, not just the shell:

```bash
py -c "from app.modules.documents.services.ocr_service import tesseract_available; print(tesseract_available())"
```

**Without this, OCR fails with `OCR_UNAVAILABLE`** — it does not silently produce empty text.

## Project setup

### Backend

```bash
cd backend
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # then fill in the values
alembic upgrade head
```

**Upgrading an install that predates Release 2.1?** Keep `GEMINI_API_KEY` and `GEMINI_MODEL` in
`.env` for this one run, and set `ENCRYPTION_KEYS` first. The migration encrypts the key into
`llm_providers` and makes it the active provider; both variables can be deleted afterwards. With
no `ENCRYPTION_KEYS` the migration refuses rather than storing a credential in plaintext, and with
no `GEMINI_API_KEY` it seeds nothing and the system reports having no provider.

```bash
py scripts\create_admin.py      # prompts for email + password
```

`pip install` takes a long time — on the order of 30-45 minutes on a cold cache. Most of it is
CrewAI's dependency tree (chromadb, lancedb, onnxruntime, pyarrow, torch). It is not stuck.

The embedding model (~420 MB) is downloaded on first use rather than at install time, so the
first document processed is slow. To pre-warm:

```bash
py -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-mpnet-base-v2')"
```

### Frontend

```bash
cd frontend
npm install
copy .env.example .env.local
```

Node 20.9.0 is exactly Next.js 16's floor. If anything odd appears during install, upgrade Node
before investigating further.

## Environment

`backend/.env` — none of these may ever reach the browser:

```
DATABASE_URL=postgresql+psycopg://user:<REDACTED>@localhost:5432/enterprise_rag
REDIS_URL=redis://localhost:6379/0
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=documents

ENCRYPTION_KEYS=1:<REDACTED>
ENCRYPTION_ACTIVE_KEY_ID=1
LLM_CONFIG_STALENESS_SECONDS=300

JWT_SECRET=<REDACTED>
ACCESS_TOKEN_MINUTES=30
REFRESH_TOKEN_DAYS=7

EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
EMBEDDING_DIM=768

TESSERACT_CMD=
OCR_MIN_CHARS_PER_PAGE=50

STORAGE_ROOT=./storage
MAX_DOCUMENT_SIZE_MB=20
MAX_IMAGE_SIZE_MB=2
CHUNK_SIZE_CHARS=1000
CHUNK_OVERLAP_CHARS=150
RETRIEVAL_TOP_K=5
RETRIEVAL_MIN_SCORE=0.35
SEMANTIC_DUPLICATE_ENABLED=false
SEMANTIC_DUPLICATE_THRESHOLD=0.95
```

`frontend/.env.local`:

```
BACKEND_URL=http://localhost:8000
```

`BACKEND_URL` is deliberately **not** `NEXT_PUBLIC_`. Only the server-side route handlers need
it, and any `NEXT_PUBLIC_*` value is inlined into the client bundle at build time — which
publishes it. No secret may ever carry that prefix.

## Startup order

Services first, then dependants. FastAPI creates the Qdrant collection at startup, so Qdrant must
already be listening.

| # | Terminal | Command | Port |
| - | -------- | ------- | ---- |
| — | PostgreSQL | Windows service | 5432 |
| — | Memurai | Windows service | 6379 |
| 1 | Qdrant | `C:\qdrant\qdrant.exe` | 6333 / 6334 |
| 2 | FastAPI | `venv\Scripts\activate && uvicorn app.main:app --reload --port 8000` | 8000 |
| 3 | Celery | `venv\Scripts\activate && celery -A app.workers.celery_app worker -l info --pool=solo` | — |
| 4 | Flower | `venv\Scripts\activate && celery -A app.workers.celery_app flower --port=5555` | 5555 |
| 5 | Next.js | `npm run dev` | 3000 |

`scripts/*.bat` will wrap each of these.

`--pool=solo` is required on Windows and processes **one document at a time**. Switch to
`--pool=threads -c 4` when throughput matters.

## Verification

1. <http://localhost:6333/dashboard> — Qdrant loads
2. `curl http://localhost:8000/health` → ok
3. <http://localhost:8000/docs> lists auth, documents and chat routers
4. <http://localhost:5555> — Flower shows one worker
5. <http://localhost:3000> — login renders
6. Sign in with the created admin
7. Upload `valid.pdf` — the response returns immediately with a `task_id`
8. Watch Flower pick the task up; the dashboard queue advances through stages
9. Qdrant dashboard shows points in `documents`
10. Ask a question in chat about that document — grounded answer with a source chip
11. Ask something unrelated — *"I could not find this information…"*, no sources

Step 7 is the one that proves §20: if the response takes 30 seconds, processing has leaked onto
the request path.

## Troubleshooting

| Symptom | Cause |
| ------- | ----- |
| `OCR_UNAVAILABLE` | Tesseract not on `PATH`; set `TESSERACT_CMD` |
| Documents stuck at `QUEUED` | Worker not running, or pointed at a different Redis database |
| `SEARCH_FAILED` on every chat | Qdrant not running |
| Collection empty after restart | Something called `recreate_collection` — see [ADR-006](../architecture/decisions/ADR-006-qdrant-collection-lifecycle.md) |
| Worker exits immediately on start | Missing `--pool=solo` |
| Memurai stops after ~10 days | Developer Edition restart requirement |
| First document takes minutes | Embedding model downloading — pre-warm it |
| 401 on every request | Access token expired; the BFF should refresh |

## Production — not yet specified

**Memurai Developer Edition cannot be used in production.** Options: Linux with real Redis,
WSL2, or a Memurai Enterprise licence.

Also unresolved for production: HTTPS termination, real Celery concurrency (`prefork` on Linux),
Flower behind authentication, Qdrant with an API key, backups for PostgreSQL and Qdrant, and log
rotation. A `docker-compose.yml` can be added without touching application code — nothing depends
on the runtime being native.

---

## Running the tests

```bash
cd backend
py -m pytest tests -q            # ~14 min. Needs PostgreSQL, Redis and Qdrant running.

cd ../frontend
npm test                         # ~10 s
npm run build                    # type-checks everything; treat as a gate
```

The backend suite uses the `enterprise_rag_test` database and the `documents_test` Qdrant
collection, never the development ones.

**Run one suite at a time.** Every suite truncates the shared test database between tests, so two
pytest processes at once will delete each other's rows and produce failures that have nothing to
do with the code.

The grounding evaluation is opt-in and costs real Gemini calls:

```bash
set RUN_GROUNDING_EVAL=1
py -m pytest tests/test_grounding_eval.py -v
```
