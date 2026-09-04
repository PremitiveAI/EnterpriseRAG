# Celery Tasks

§23: tasks must be retry-safe, idempotent, observable, timeout-bounded and properly logged.

## Configuration

```python
# app/workers/celery_app.py
broker_url            = settings.REDIS_URL          # Memurai on Windows
result_backend        = settings.REDIS_URL
task_serializer       = "json"
accept_content        = ["json"]
timezone              = "UTC"
enable_utc            = True

task_acks_late           = True    # ack after completion, not on receipt
task_reject_on_worker_lost = True  # redeliver if the worker dies mid-task
worker_prefetch_multiplier = 1     # do not hoard messages
task_track_started       = True    # STARTED state visible in Flower
```

`task_acks_late = True` is the important one: with early ack, a worker killed mid-document loses
the task silently and the document sits at `PROCESSING` forever. With late ack it is redelivered,
and idempotency makes the rerun safe.

`worker_prefetch_multiplier = 1` matters because these tasks are minutes long, not milliseconds.
The default would let one worker reserve a queue of documents it cannot start for an hour.

## Windows

```
celery -A app.workers.celery_app worker -l info --pool=solo
```

Celery has not officially supported Windows since v4. `--pool=solo` is correct but processes one
task at a time — one document at a time, system-wide.

`--pool=threads -c 4` gives real concurrency and suits this pipeline, which is I/O- and
subprocess-bound (Tesseract, file reads, HTTP to Qdrant) rather than CPU-bound in Python. The
one caution is the embedding model: it loads once per process, so threads share it — which is
desirable, but the encode call must not be assumed thread-safe without checking.

Start with `solo`. Move to `threads` when throughput matters.
See [ADR-007](../architecture/decisions/ADR-007-native-windows-services.md).

## Tasks

### `process_document(document_id: str)`

The only task that matters. Full pipeline in
[../features/document-processing.md](../features/document-processing.md).

```python
@celery_app.task(
    bind=True,
    name="documents.process",
    max_retries=3,
    soft_time_limit=1800,   # 30 min — raises, lets the task clean up
    time_limit=2100,        # 35 min — hard kill
    autoretry_for=(),       # retries are explicit, never blanket
)
def process_document(self, document_id: str): ...
```

**`autoretry_for` is deliberately empty.** A blanket auto-retry would re-run a corrupt PDF three
times and burn 90 minutes of a single-slot worker to reach the same conclusion. Each stage
decides for itself whether its failure is transient — see the retry table in the processing doc.

Two time limits: `soft_time_limit` raises `SoftTimeLimitExceeded` inside the task, so it can mark
the document `FAILED` with a real error code. `time_limit` is the backstop for a task that
ignores the soft signal. Without the soft limit, a hung OCR would be killed with the document
still showing `OCR` forever.

### `cleanup_orphaned_files()`

Scheduled, hourly. Finds blobs in storage with no `documents` row and removes them — the residue
of crashes between blob write and commit. Logs what it deletes; never touches a blob younger than
one hour, so it cannot race an in-flight upload.

### `purge_deleted_documents()`

Scheduled, daily. For documents soft-deleted more than `RETENTION_DAYS` ago: delete the blob,
confirm no vectors remain, keep the row. The row is retained permanently so `message_sources`
citations in historical answers always resolve.

## Retry policy

```python
try:
    vector_service.upsert(chunks)
except QdrantUnavailable as exc:
    raise self.retry(exc=exc, countdown=2 ** self.request.retries * 10)
```

Exponential backoff: 10 s, 20 s, 40 s. Enough for a restarting Qdrant, short enough that a
genuine outage surfaces within a minute.

| Failure | Retry | Why |
| ------- | :---: | --- |
| Qdrant unreachable | 3× | Almost always transient |
| Embedding error | 2× | Usually memory pressure |
| Gemini / agent error | 2× | Rate limits and transient 5xx |
| OCR error | 1× | Occasionally a resource issue |
| Corrupt file | ❌ | Deterministic |
| Unsupported format | ❌ | Deterministic |
| Missing blob | ❌ | Retrying cannot conjure a file |

On exhaustion: `status = FAILED`, `error_code` set, `retry_count` persisted. The admin can
reprocess manually — the document is not lost, only unindexed.

## Idempotency

Every task must converge when run twice (§23):

| Resource | Mechanism |
| -------- | --------- |
| Qdrant points | `uuid5(NAMESPACE, f"{document_id}:{chunk_index}")` — upsert replaces |
| Stale vectors | Deleted by `document_id` payload filter before re-index |
| `document_chunks` | Deleted for the document, then re-inserted |
| `document_tags` | Replaced, never appended |
| Status | Guarded by the state machine |
| Audit | Append-only by design; duplicates are acceptable and truthful |

**Never** `client.upsert()` with random ids, and never `recreate_collection`
([ADR-006](../architecture/decisions/ADR-006-qdrant-collection-lifecycle.md)).

## Enqueueing

```python
db.commit()                                   # first
process_document.delay(str(document.id))      # then
```

Order is not stylistic. Enqueue before commit and a fast worker can pick up a `document_id` that
is not yet visible in its own transaction, and fail with "not found" on a document that exists.

## Observability

Flower on `:5555` shows active, queued, succeeded and failed tasks, plus arguments and runtime.

> ⚠️ Flower has **no authentication by default**. It must bind to `127.0.0.1` and never be
> exposed. It displays task arguments, which include document ids.

Structured logs carry `task_id`, `document_id`, `stage`, `duration_ms`, `retry_count` (§39).
`document_processing.task_id` links a database row to its Flower entry.

## Known constraints

1. **`--pool=solo` means one document at a time.** A 500-page scan blocks the queue for minutes.
2. **`result_backend` on Redis grows.** Set `result_expires` (default 24 h) or results accumulate.
3. **Scheduled tasks need Celery Beat** — a sixth terminal. Deferred until cleanup and purge are
   actually needed; both can be run manually from `scripts/` in the meantime.
4. **The embedding model loads per worker process** (~500 MB). Restarting the worker costs the
   model load time before the first task runs.
