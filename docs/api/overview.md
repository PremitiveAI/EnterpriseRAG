# API Overview

All endpoints are versioned under `/api/v1` (§37).

Base URL in development: `http://localhost:8000/api/v1`

## Authentication

JWT bearer on every request except `POST /auth/login` and `GET /health`.

```
Authorization: Bearer <access_token>
```

Single admin role — a valid token grants full access
([ADR-001](../architecture/decisions/ADR-001-single-admin-role.md)).

| Token | Lifetime | Storage |
| ----- | -------- | ------- |
| Access | 30 min | Memory in the Next.js BFF layer — **never** the browser |
| Refresh | 7 days | `httpOnly`, `Secure`, `SameSite=Strict` cookie |

The browser never sees a raw backend token. Next.js route handlers hold it server-side and proxy
(§40, §42).

## Response envelope

**Success** — HTTP 2xx:

```json
{ "success": true, "data": { }, "message": "optional" }
```

**Failure** — a real 4xx/5xx status, matching `error_code`:

```json
{
  "success": false,
  "error_code": "DOCUMENT_NOT_FOUND",
  "message": "No document with that id.",
  "details": { },
  "request_id": "01J8…"
}
```

The HTTP status is always meaningful. Errors are never returned with HTTP 200.

## Endpoints

### Auth

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/auth/login` | Email + password → tokens |
| POST | `/auth/refresh` | Refresh cookie → new access token |
| POST | `/auth/logout` | Revoke refresh token |
| GET | `/auth/me` | Current admin |

### Documents

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/admin/documents/upload` | Multi-file upload — **partial success**, per-file results |
| GET | `/admin/documents` | List — search, filter, sort, paginate |
| GET | `/admin/documents/{id}` | Detail with tags, processing info, chunk count |
| GET | `/admin/documents/{id}/status` | Lightweight polling target (§44) |
| GET | `/admin/documents/{id}/download` | Stream the source file |
| PATCH | `/admin/documents/{id}` | Metadata-only edit — no re-embedding (§29) |
| DELETE | `/admin/documents/{id}` | Soft delete + vector removal (§30) |
| POST | `/admin/documents/{id}/reprocess` | Re-run the pipeline |
| GET | `/admin/documents/categories` | Filter options — active taxonomy plus statuses, document types and languages |
| GET | `/config/upload-limits` | Limits, so client validation cannot drift |

### Chat

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/chat/conversations` | Create |
| GET | `/chat/conversations` | List, grouped by recency — empty conversations excluded |
| GET | `/chat/conversations/{id}` | Detail with messages and sources |
| PATCH | `/chat/conversations/{id}` | Rename |
| DELETE | `/chat/conversations/{id}` | Soft delete |
| POST | `/chat/conversations/{id}/messages` | Ask — returns answer + sources |

### Super Admin — agent rules

> **Designed, not built.** [ai/agent-rules.md](../ai/agent-rules.md) §7 ·
> [ADR-009](../architecture/decisions/ADR-009-dynamic-agent-rules.md). The other Super Admin
> endpoints are specified in [release-2/features/](../release-2/features/).

Requires `subject_type = SUPER_ADMIN`. An Organization Admin token is 403 before any handler runs.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/super-admin/agent-rules` | List agents and whether each has a custom rule |
| GET | `/super-admin/agent-rules/{agent_key}` | One rule, plus the read-only text code appends |
| PUT | `/super-admin/agent-rules/{agent_key}` | Save. Creates on first write — "add" and "edit" are one call |

**There is no DELETE, and the absence is deliberate.** Emptying the content restores the shipped
prompt, which is the only thing a delete would have done. A second route reaching the same state
would be a second thing to authorize. `DELETE` returns 405 because no handler exists, not
because one refuses.

`{agent_key}` is a registry key — `query_planner`, `response_composer` — **never a path or a
filename**. An unrecognised key is 404 without a filesystem lookup, so path traversal is not
filtered here; it is unrepresentable. Compare `LocalStorageService._path()`, which does resolve
and containment-check, because there the key genuinely comes from a client.

## List query parameters

`GET /admin/documents` (§28):

| Parameter | Type | Notes |
| --------- | ---- | ----- |
| `search` | string | Matches `title` and `original_file_name` |
| `status` | enum, repeatable | |
| `category_id` | UUID, repeatable | |
| `document_type` | string | |
| `language` | string | |
| `tag` | string | Exact match on a single tag |
| `created_from` / `created_to` | date | |
| `sort` | enum | `created_at`, `file_name`, `file_size`, `status` |
| `order` | `asc` / `desc` | default `desc` |
| `page` / `page_size` | int | default 1 / 25; `page_size` is clamped to 100, not rejected |
| `include_deleted` | bool | default false |

Response carries `{ items, page, page_size, total, total_pages }`. Soft-deleted rows are excluded
unless `include_deleted=true`.

## Status polling (§44)

`GET /admin/documents/{id}/status` is deliberately small — no joins, no chunk counts:

```json
{ "success": true,
  "data": { "document_id": "…", "status": "EMBEDDING", "current_stage": "embedding",
            "progress_percent": 70, "retry_count": 0, "error_code": null } }
```

The client stops polling at `COMPLETED`, `FAILED`, `DUPLICATE` or `DELETED`. Recommended
interval: 2 s, backing off to 5 s after 30 s.

`progress_percent` is derived from the stage's position in the pipeline, not measured — it is a
UI affordance, not a metric.

The endpoint shape is intentionally compatible with a later SSE or WebSocket push: the payload is
already a complete state snapshot, so a transport change needs no contract change.

## Rate limits (§40)

| Scope | Limit |
| ----- | ----- |
| `POST /auth/login` | 5 / minute / IP |
| `POST /admin/documents/upload` | 20 / minute / user |
| `POST /chat/.../messages` | 30 / minute / user |
| Everything else | 300 / minute / user |

Exceeding returns `429` with `RATE_LIMIT_EXCEEDED` and a `Retry-After` header.

## Contract stability

Changing a shipped contract requires documenting the impact first (§37). Additive fields are safe;
renaming or removing a field, or changing a type or an `error_code`, is breaking and needs a new
version.
