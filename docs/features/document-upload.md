# Feature — Document Upload

## 1. Requirement

Multi-file upload with independent client and server validation, exact-duplicate detection, and
an immediate response that never waits for processing (§15–§20).

## 2. Business rules

- Supported: **PDF, DOCX, PPTX, TXT, JPG, JPEG, PNG, WEBP**.
- **`.doc` and `.ppt` are rejected** with `LEGACY_FORMAT_UNSUPPORTED` — parsing them needs a
  LibreOffice dependency, deliberately not taken in v1 (§15 approved deviation).
- Limits: **20 MB** documents, **2 MB** images. Configurable, defined once.
- A batch is **partially successful** (§19). One bad file never fails the others.
- The response must not wait for OCR, extraction, chunking, embedding, indexing, Gemini or
  CrewAI (§20).
- Client validation is UX only. **It is not a security boundary** (§16).

## 3. User flow

```
/upload → drag & drop or browse → per-file validation runs immediately
        → invalid files marked, never sent
        → "Upload Documents" → per-file result
        → accepted files begin polling their status
```

Per-file states in the list: `validating → ready | invalid → uploading → queued | duplicate |
rejected → processing → completed | failed`.

## 4. Backend flow

```
POST /api/v1/admin/documents/upload      multipart, field: files[]
  ├─ authenticate
  ├─ reject if count > MAX_FILES_PER_BATCH
  └─ per file, independently:
        1  sanitise filename        strip path separators, control chars, cap 255 bytes
        2  extension allow-list
        3  MIME — server-derived from content, never the client's claim (§17)
        4  magic bytes vs extension
        5  size vs class limit
        6  non-empty
        7  SHA-256 of the bytes
        8  in-batch comparison against earlier files in this request
        9  database lookup on file_hash among live rows
       10  persist documents row (status QUEUED) + document_processing row
       11  write the blob via StorageService → storage_key
       12  enqueue process_document(document_id)
       13  audit document.upload
  └─ 200 with per-file results
```

Steps 1–9 are pure CPU and I/O — tens of milliseconds. Nothing in this path calls an AI model.

**Order matters.** Validation precedes hashing, hashing precedes any write, and the blob is
written only after the row exists — so a crash mid-request can leave an orphaned row (visible,
recoverable) but never an orphaned blob (invisible, leaks disk).

## 5. Frontend flow

`/upload` (Next.js App Router). Client validation mirrors the server using limits fetched from
`GET /config/upload-limits`, so the two cannot drift. The file is posted to a Next.js route
handler which attaches the bearer token server-side and streams to FastAPI.

## 6. Database impact

Inserts into `documents`, `document_processing`, `audit_logs`. Reads `documents.file_hash`.

## 7. API contract

Request: `multipart/form-data`, repeated `files` parts.

```json
{
  "success": true,
  "message": "3 accepted, 1 duplicate, 1 rejected.",
  "data": {
    "accepted": 3, "duplicates": 1, "rejected": 1,
    "results": [
      { "file_name": "q3-report.pdf", "status": "QUEUED",
        "document_id": "…", "task_id": "…" },
      { "file_name": "q3-report-copy.pdf", "status": "DUPLICATE",
        "error_code": "DUPLICATE_DOCUMENT",
        "duplicate_of": { "document_id": "…", "file_name": "q3-report.pdf" } },
      { "file_name": "diagram.bmp", "status": "REJECTED",
        "error_code": "INVALID_FILE_TYPE",
        "message": "BMP is not supported." }
    ]
  }
}
```

HTTP **200** even with rejections — the batch itself succeeded. A file-level failure is data, not
a transport error. `400` is returned only when the whole request is invalid (no files, too many).

## 8. Celery impact

One `process_document` task enqueued per accepted file, after commit. Enqueueing before the
transaction commits would let the worker start on a row it cannot yet see.

## 9. Qdrant impact

None at upload. Indexing happens in the task.

## 10. Redis impact

Broker only — the task message.

## 11. Security considerations

- Filenames sanitised; path traversal (`../`, absolute paths, NUL, drive letters) rejected.
- The stored name is generated; the original is kept for display only and **never** used to build
  a path.
- MIME is derived from content. The client's `Content-Type` is ignored entirely.
- Magic-byte check catches a renamed executable.
- Size enforced server-side and at the ASGI layer, so a 2 GB body is refused before it is buffered.
- Upload is admin-only and rate-limited.
- Filenames are logged; **file contents never are**.

## 12. Error handling

Per file: `INVALID_FILE_TYPE`, `LEGACY_FORMAT_UNSUPPORTED`, `INVALID_MIME_TYPE`,
`FILE_SIGNATURE_MISMATCH`, `FILE_TOO_LARGE`, `FILE_EMPTY`, `INVALID_FILENAME`,
`DUPLICATE_DOCUMENT`, `DUPLICATE_IN_BATCH`.

Whole request: `NO_FILES_PROVIDED`, `TOO_MANY_FILES`, `UNAUTHORIZED`, `RATE_LIMIT_EXCEEDED`.

## 13. Edge cases

| Case | Behaviour |
| ---- | --------- |
| Same file twice in one batch | First accepted, second `DUPLICATE_IN_BATCH` |
| File matching a **soft-deleted** document | **Accepted.** Re-upload after deletion is legitimate |
| Identical bytes, different filenames | Duplicate — the hash is of content, not name |
| `.pdf` extension on a PNG | `FILE_SIGNATURE_MISMATCH` |
| Unicode / emoji filename | Accepted, sanitised, original preserved for display |
| Filename over 255 bytes | Truncated for storage, original kept |
| Disk full at step 11 | Row rolled back, `500`, no orphan |
| Connection drops mid-upload | Nothing persisted; the request never completed |
| Zero-byte file | `FILE_EMPTY` |
| 20 MB exactly | Accepted — the limit is inclusive |

## 14. Testing requirements

Every row of §46's upload list: valid, invalid extension, invalid MIME, bad signature, oversized,
empty, malformed, multi-file, in-batch duplicate, exact duplicate. Plus: partial-batch success
returns per-file results; the endpoint returns in well under a second for a 20 MB PDF; no AI call
occurs on the request path; re-upload of a soft-deleted document is accepted.

## 15. Acceptance criteria

- [ ] Multiple files upload in one request
- [ ] Client validation blocks invalid files before sending
- [ ] Server re-validates everything independently
- [ ] Both size limits enforced
- [ ] Exact duplicates detected synchronously
- [ ] One invalid file never fails the batch
- [ ] Response returns immediately, with a `task_id` per accepted file
- [ ] Path traversal is impossible
- [ ] Every upload is audited
