# Database Schema

PostgreSQL is the authoritative relational source (§9). Qdrant holds only vectors and their
payload; Redis holds only broker data, cache and recent chat context. Neither is ever the source
of truth.

Ten tables. All are proposed — no migration has been created.

## Conventions

| Rule | Value |
| ---- | ----- |
| Primary keys | `UUID` (`uuid4`), server default `gen_random_uuid()` |
| Timestamps | `TIMESTAMPTZ`, never naive |
| Every table | `created_at`, `updated_at` |
| Soft delete | `deleted_at TIMESTAMPTZ NULL` on `documents` and `conversations` |
| Enums | Native PostgreSQL `ENUM` types, not free-text |
| Migrations | Alembic only. **No `create_all()` in application code** |

## ERD

```
users ──┬──< documents ──┬──1:1── document_processing
        │                ├──< document_tags
        │                ├──< document_chunks
        │                └──self-FK duplicate_of_document_id
        │
        ├──< conversations ──< chat_messages ──< message_sources >── documents
        │
        └──< audit_logs

document_categories ──< documents
```

---

## `users`

Single admin role ([ADR-001](../architecture/decisions/ADR-001-single-admin-role.md)) — no
`role` column in v1.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `email` | VARCHAR(255) | **UNIQUE**, lowercased on write |
| `password_hash` | VARCHAR(255) | bcrypt or argon2. Never the password |
| `full_name` | VARCHAR(255) | |
| `is_active` | BOOLEAN | default `true`; false blocks login |
| `last_login_at` | TIMESTAMPTZ NULL | |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

**Index:** `UNIQUE(email)`.

The first admin is created by `scripts/create_admin.py`, never by a public endpoint — there is
no registration route (§40, admin-only).

---

## `document_categories`

Taxonomy is configurable (§24) and must not be hard-coded into scattered logic.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `name` | VARCHAR(120) | **UNIQUE** — e.g. "Legal & Compliance" |
| `slug` | VARCHAR(120) | **UNIQUE**, machine key used in the Qdrant payload |
| `description` | TEXT NULL | Fed to the classification prompt |
| `is_active` | BOOLEAN | default `true` |
| `sort_order` | INTEGER | |

Seeded from `config/taxonomy.py` with §24's ten categories. The classifier receives the active
rows and must return one of their slugs — never free text.

---

## `documents`

The central table.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `file_name` | VARCHAR(255) | Sanitised, stored name |
| `original_file_name` | VARCHAR(255) | Exactly as uploaded — display only, never a path |
| `file_type` | VARCHAR(16) | Normalised extension |
| `mime_type` | VARCHAR(128) | **Server-derived**, never the client's claim |
| `file_size` | BIGINT | Bytes |
| `storage_key` | VARCHAR(512) | Opaque key for the storage service — **not an absolute path** (§45) |
| `file_hash` | CHAR(64) | SHA-256 of raw bytes |
| `content_hash` | CHAR(64) NULL | SHA-256 of normalised text — **null until extraction** |
| `category_id` | UUID FK NULL | → `document_categories.id` |
| `document_type` | VARCHAR(64) NULL | Finer than category, e.g. `pan_card` |
| `language` | VARCHAR(8) NULL | ISO 639-1 |
| `title` | VARCHAR(512) NULL | AI-extracted |
| `description` | TEXT NULL | AI-extracted |
| `page_count` | INTEGER NULL | |
| `status` | ENUM `document_status` | See below |
| `duplicate_of_document_id` | UUID FK NULL | Self-FK ([ADR-005](../architecture/decisions/ADR-005-duplicate-detection-split.md) b) |
| `is_possible_duplicate` | BOOLEAN | Semantic level 3, review flag only |
| `duplicate_similarity` | REAL NULL | Score when the flag is set |
| `created_by` | UUID FK | → `users.id` |
| `created_at` / `updated_at` | TIMESTAMPTZ | |
| `deleted_at` | TIMESTAMPTZ NULL | Soft delete (§30) |

**Indexes** (§41): `file_hash`, `content_hash`, `status`, `document_type`, `category_id`,
`created_at`, `deleted_at`, and `(status, deleted_at)` for the list query.

`file_hash` is **not** globally unique — a re-upload after deletion is legitimate. Uniqueness is
enforced by query against live rows, not by a database constraint.

### `document_status` enum

```
UPLOADING  VALIDATING  DUPLICATE_CHECK  QUEUED  PROCESSING  EXTRACTING  OCR
CLASSIFYING  CHUNKING  EMBEDDING  INDEXING  COMPLETED  FAILED  DUPLICATE  DELETED
```

§21 lists both coarse and stage-level values. Resolution: `status` holds the **coarse lifecycle
state**, and `document_processing.current_stage` holds the fine-grained stage. Transitions are
validated by a state machine, never assigned freely — see
[celery/state-machine.md](../celery/state-machine.md).

---

## `document_processing`

1:1 with `documents`. Separated so the hot list query never reads processing detail.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `document_id` | UUID FK UNIQUE | → `documents.id` ON DELETE CASCADE |
| `task_id` | VARCHAR(155) NULL | Celery task id |
| `current_stage` | VARCHAR(32) NULL | Fine-grained stage |
| `processing_started_at` | TIMESTAMPTZ NULL | |
| `processing_completed_at` | TIMESTAMPTZ NULL | |
| `processing_error` | TEXT NULL | Operator-facing message — **never a raw stack trace** (§38) |
| `error_code` | VARCHAR(64) NULL | Machine-readable |
| `retry_count` | INTEGER | default 0 |
| `duration_ms` | INTEGER NULL | |

**Index:** `task_id` (§41).

---

## `document_tags`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `document_id` | UUID FK | ON DELETE CASCADE |
| `tag` | VARCHAR(64) | Lowercased, trimmed |
| `source` | ENUM(`ai`,`manual`) | Distinguishes generated from curated |

**Index:** `UNIQUE(document_id, tag)`, plus `tag` for filtering.

A join table rather than an array column, so tags are filterable and countable in SQL.

---

## `document_chunks`

Every chunk traces to its source (§26).

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `document_id` | UUID FK | ON DELETE CASCADE |
| `chunk_index` | INTEGER | 0-based, ordered within the document |
| `text` | TEXT | The chunk as embedded |
| `char_count` | INTEGER | |
| `page_number` | INTEGER NULL | |
| `section` | VARCHAR(255) NULL | Heading, where derivable |
| `language` | VARCHAR(8) NULL | |
| `vector_point_id` | UUID | Deterministic — mirrors the Qdrant point id |
| `metadata` | JSONB | Extraction-specific extras |

**Index:** `UNIQUE(document_id, chunk_index)`, plus `vector_point_id`.

Storing chunk text in PostgreSQL as well as Qdrant is deliberate: it makes re-indexing possible
without re-parsing the source, and keeps citations resolvable if Qdrant is unavailable.

---

## `conversations`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `user_id` | UUID FK | → `users.id` |
| `title` | VARCHAR(255) | Derived from the first message |
| `message_count` | INTEGER | Denormalised for the sidebar |
| `last_message_at` | TIMESTAMPTZ NULL | Drives Today / Yesterday / Previous 7 Days grouping |
| `created_at` / `updated_at` | TIMESTAMPTZ | |
| `deleted_at` | TIMESTAMPTZ NULL | |

**Index:** `(user_id, last_message_at DESC)` — exactly the sidebar query.

---

## `chat_messages`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `conversation_id` | UUID FK | ON DELETE CASCADE |
| `role` | ENUM(`user`,`assistant`,`system`) | |
| `content` | TEXT | |
| `token_count` | INTEGER NULL | |
| `latency_ms` | INTEGER NULL | Assistant messages |
| `retrieval_count` | INTEGER NULL | Chunks retrieved |
| `is_grounded` | BOOLEAN NULL | False when the model declined for lack of context (§34) |
| `error_code` | VARCHAR(64) NULL | |
| `created_at` | TIMESTAMPTZ | |

**Index:** `(conversation_id, created_at)` (§41).

---

## `message_sources`

Citations (§35). A separate table, not JSON, so "which documents get cited most" is a query.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `message_id` | UUID FK | ON DELETE CASCADE |
| `document_id` | UUID FK | → `documents.id` |
| `chunk_id` | UUID FK NULL | → `document_chunks.id` |
| `page_number` | INTEGER NULL | |
| `score` | REAL | Similarity at retrieval time |
| `rank` | INTEGER | Position in the retrieved set |

`document_id` uses `ON DELETE RESTRICT`. Documents are soft-deleted, so a citation always
resolves — historical answers keep their references even after a document leaves the index.

---

## `audit_logs`

§22, §40.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `user_id` | UUID FK NULL | Null for system actions |
| `action` | VARCHAR(64) | `document.upload`, `document.delete`, `auth.login_failed`, … |
| `entity_type` | VARCHAR(64) | |
| `entity_id` | UUID NULL | |
| `request_id` | VARCHAR(64) NULL | Correlates with structured logs (§39) |
| `ip_address` | INET NULL | |
| `user_agent` | VARCHAR(512) NULL | |
| `metadata` | JSONB | **Must never contain file contents, PII or secrets** (§39) |
| `created_at` | TIMESTAMPTZ | |

**Index:** `(entity_type, entity_id)`, `(user_id, created_at DESC)`, `action`.

Append-only. No update or delete path is exposed.

---

## `llm_providers`

Release 2.1. Which model answers, and the encrypted credential it answers with
([ADR-010](../architecture/decisions/ADR-010-database-backed-llm-credentials.md)).

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID | PK |
| `provider_name` | enum | `gemini` · `openai` · `anthropic` · `azure` |
| `model_name` | VARCHAR(120) | **Bare** id. The `provider/` prefix is composed at call time |
| `encrypted_api_key` | TEXT | `v1.<key id>.<base64url>` — AES-256-GCM |
| `encryption_key_id` | SMALLINT | Which key wrote it; lets a rotation find its work without decrypting |
| `key_fingerprint` | VARCHAR(16) | `sha256(plaintext)[:16]` — what the UI and the audit log compare |
| `base_url` | VARCHAR(500) | Azure and self-hosted endpoints |
| `config` | JSONB | Temperature, timeouts. Per-agent settings win over these |
| `is_active` | BOOLEAN | Exactly one true |
| `config_version` | BIGINT | From `llm_config_version_seq`, monotonic across **all** rows |
| `last_tested_at` | TIMESTAMPTZ | Bookkeeping — does **not** bump the version |
| `created_by` | UUID | → `users.id`, `ON DELETE SET NULL`. Null for the seeded row |

Three database objects carry the design, and each replaces application logic that would fail
silently:

```sql
CREATE SEQUENCE llm_config_version_seq;

CREATE UNIQUE INDEX ix_llm_providers_one_active
  ON llm_providers (is_active) WHERE is_active;

CREATE TRIGGER trg_llm_providers_bump_version BEFORE UPDATE ON llm_providers
  FOR EACH ROW WHEN (/* any column that changes the resolved config */)
  EXECUTE FUNCTION llm_providers_bump_version();
```

- The **partial unique index** means two concurrent activations cannot both win.
- The **global sequence** means two rows can never hold the same version. Per-row counters can, and
  a process warm on the wrong one then compares equal and never rebuilds — answering from the wrong
  vendor with nothing raised anywhere.
- The **trigger** sets the version on every update that changes the resolved configuration. In
  application code, one forgotten assignment in one write path reproduces that same collision. Its
  `WHEN` clause excludes `last_tested_at`, so pressing Test does not make every process rebuild a
  client that did not change.

Activation order is not arbitrary: the current row is deactivated **before** the new one is
activated, because the index is checked per statement.

## Deferred

- Partitioning `audit_logs` by month — unnecessary below millions of rows.
- Full-text search on `documents.title` — the list filter uses `ILIKE`; add `pg_trgm` if it slows.
- A `document_versions` table — §29 reprocesses in place; versioning is not required.
