# Feature — Document Management

## 1. Requirement

Search, filter, sort, paginate, view, edit, delete, reprocess and download documents (§28–§30).

**No design exists for this screen.** The Stitch dashboard has a 4-row static "Recent Documents"
table only. The UI is derived from `DESIGN.md` and that table's visual language — see
[../frontend/screens.md](../frontend/screens.md).

## 2. Business rules

- Deletion is **soft** (§30). The row survives so citations resolve.
- Deleting removes the document's vectors immediately — deleted documents must never appear in
  retrieval.
- **Metadata-only edits do not regenerate embeddings** (§29).
- Reprocessing removes existing vectors before re-indexing, so nothing stale survives.
- A document being processed cannot be edited or reprocessed concurrently.
- Every mutation is audited.

## 3. User flow

```
/documents  →  table: name · type · category · status · size · uploaded
            →  search box, filter bar, sortable headers, pagination
            →  row → detail drawer
                     ├─ Edit      title, description, category, tags
                     ├─ Download  original file
                     ├─ Reprocess re-run the pipeline
                     └─ Delete    confirm → soft delete
```

Status renders as a coloured chip, matching the dashboard's `Indexed` / `Processing` / `Failed`
treatment. Failed rows expose their `error_code` and a Reprocess action, so the fix is one click
from the diagnosis.

## 4. Backend flow

### List

```
GET /admin/documents?search=&status=&category_id=&page=&sort=
  ├─ base: deleted_at IS NULL
  ├─ search  → ILIKE on title and original_file_name
  ├─ filters → status, category, document_type, language, date range
  ├─ sort    → whitelist only (created_at, file_name, file_size, status)
  ├─ paginate
  └─ count + items
```

`sort` is whitelisted rather than interpolated. A column name taken from a query string and
dropped into `ORDER BY` is an injection vector even when the values are parameterised.

### Edit — metadata only

```
PATCH /admin/documents/{id}
  ├─ reject if status is an active processing state
  ├─ update title, description, category_id, tags
  ├─ update the Qdrant PAYLOAD for this document's points   ← not the vectors
  ├─ audit document.updated
  └─ 200
```

The payload update is easy to forget and its absence is subtle: the vectors stay correct, so
search still works, but citations render the **old document name** and category filters use the
old value. Renaming without it produces a system that is right in the database and wrong on
screen.

Editing metadata never re-embeds. The text has not changed (§29).

### Delete

```
DELETE /admin/documents/{id}
  ├─ reject if already deleted → DOCUMENT_ALREADY_DELETED
  ├─ documents.deleted_at = now(), status = DELETED
  ├─ delete Qdrant points by document_id filter
  ├─ storage: retain or delete per policy
  ├─ audit document.deleted
  └─ 200
```

Database first, then vectors. If the vector delete fails, the document is already invisible to
retrieval via the `status` filter — so the failure degrades safely. The reverse order would leave
a window where the vectors are gone but the document still looks live.

### Reprocess

```
POST /admin/documents/{id}/reprocess
  ├─ reject if currently processing
  ├─ delete existing Qdrant points
  ├─ delete existing document_chunks
  ├─ reset processing row, status = QUEUED
  ├─ enqueue process_document
  └─ 202 with task_id
```

Deleting old vectors **before** re-indexing is what prevents orphans when the reprocessed
document yields fewer chunks (§29).

## 5. Frontend flow

Server-rendered list with filters in the URL, so a filtered view is shareable and survives
refresh. Detail is a drawer, not a route change, keeping list state intact. Delete requires
confirmation naming the file. Reprocess starts polling immediately.

## 6. Database impact

Reads `documents` joined to `document_categories`, `document_tags`, `document_processing`.
Writes metadata, `deleted_at`, `status`, tags. Deletes `document_chunks` on reprocess. Inserts
audit rows.

The list query is the hottest in the system — served by `(status, deleted_at)` and `created_at`
indexes (§41).

## 7. API contract

Endpoints and query parameters: [../api/overview.md](../api/overview.md).

```json
{ "success": true,
  "data": {
    "items": [
      { "id": "…", "file_name": "employee-handbook-2024.docx",
        "document_type": "policy", "category": { "slug": "administrative-internal", "name": "Administrative & Internal" },
        "status": "COMPLETED", "language": "en", "file_size": 865_280,
        "page_count": 42, "tags": ["hr", "onboarding"], "chunk_count": 87,
        "is_possible_duplicate": false, "created_at": "2026-08-14T09:12:00Z" }
    ],
    "page": 1, "page_size": 25, "total": 1248, "total_pages": 50 } }
```

## 8. Celery impact

Reprocess enqueues `process_document`. Nothing else touches Celery.

## 9. Qdrant impact

| Action | Effect |
| ------ | ------ |
| List / detail | None |
| Metadata edit | **Payload update** — no re-embedding |
| Delete | Points removed by `document_id` filter |
| Reprocess | Points removed, then re-created |

## 10. Redis impact

None. The list is not cached — it changes constantly and a stale document list is worse than a
slightly slower one.

## 11. Security considerations

- All endpoints admin-only.
- `sort` and filter fields whitelisted.
- Download streams via `StorageService` using the stored `storage_key` — the client never
  supplies a path, so traversal is impossible by construction.
- `Content-Disposition: attachment` with a sanitised filename, so an HTML file cannot execute in
  the origin.
- Every mutation audited with `user_id`, `request_id` and IP.

## 12. Error handling

`DOCUMENT_NOT_FOUND` 404 · `DOCUMENT_ALREADY_DELETED` 409 · `DOCUMENT_NOT_EDITABLE` 409 ·
`DOCUMENT_NOT_REPROCESSABLE` 409 · `STORAGE_FILE_MISSING` 500 · `VALIDATION_ERROR` 422.

## 13. Edge cases

| Case | Behaviour |
| ---- | --------- |
| Delete while processing | Allowed. The task detects `deleted_at`, aborts, indexes nothing |
| Reprocess a `FAILED` document | Allowed — the primary way to recover |
| Reprocess a `DUPLICATE` | Allowed; may resolve differently if the original was deleted |
| Edit during processing | 409 — the pipeline would overwrite the edit |
| Reprocess yields fewer chunks | Old points deleted first; no orphans |
| Download a document whose blob is missing | 500 `STORAGE_FILE_MISSING` |
| Page beyond the last | Empty `items`, correct `total` |
| Filter matching nothing | Empty state, not an error |
| Category deleted while documents reference it | Category is soft-disabled, never hard-deleted |

## 14. Testing requirements

Pagination boundaries · every filter individually and combined · sort whitelist rejects an
arbitrary column · search matches title and filename · soft delete hides from list and retrieval ·
vectors removed on delete · metadata edit updates the Qdrant payload · metadata edit does **not**
change vectors · reprocess leaves no orphan chunks · concurrent edit during processing is
rejected · every mutation writes an audit row.

## 15. Acceptance criteria

- [x] List supports search, filter, sort and pagination
- [x] Detail shows metadata, tags, processing info and chunk count
- [x] Metadata edits persist and update the Qdrant payload without re-embedding
- [x] Deleted documents disappear from the list and from retrieval
- [x] Reprocess re-runs the pipeline and leaves no stale vectors
- [x] Download streams the original safely
- [x] Failed documents show their error and offer Reprocess
- [x] Every mutation is audited
