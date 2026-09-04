# Feature — Duplicate Detection

Three levels, split across two moments in time. The split is forced by the specification itself —
see [ADR-005](../architecture/decisions/ADR-005-duplicate-detection-split.md).

## 1. Requirement

Detect exact duplicates, content duplicates, and optionally semantically similar documents
(§18), without making the upload request wait for extraction (§20).

## 2. Business rules

| Level | Detects | Runs | Verdict |
| :---: | ------- | ---- | ------- |
| **1** SHA-256 | Byte-identical files | Synchronously, at upload | Rejected immediately |
| **2** Content hash | Identical normalised text | Asynchronously, after extraction | `status = DUPLICATE`, not indexed |
| **3** Semantic | Near-identical meaning | Asynchronously, **disabled by default** | Flag for review only — **never** auto-rejects |

- A document matching a **soft-deleted** document is **accepted**. Re-uploading after deletion is
  legitimate.
- Level 3 must never automatically mean duplicate (§18's own instruction).
- Content-duplicate documents are **not** chunked, embedded or indexed.

## 3. Why the split exists

Level 1 is a function of raw bytes — available in ~5 ms for a 20 MB file. Levels 2 and 3 need
*extracted text*, which is precisely what §20 forbids the request from waiting for. A 300-page
scan takes 60–90 s to OCR.

Both constraints cannot hold at one moment, so detection happens at two.

**The visible consequence:** a file reported as `QUEUED` at upload can later become `DUPLICATE`.
That is correct behaviour. `DUPLICATE` is terminal in §21, so status polling stops there.

## 4. Level 1 — SHA-256

```python
h = hashlib.sha256()
for block in iter(lambda: f.read(65536), b""):
    h.update(block)
file_hash = h.hexdigest()
```

Streamed in 64 KB blocks — a 20 MB file never sits in memory twice.

Two comparisons, in order:

1. **In-batch** — against files earlier in the same request → `DUPLICATE_IN_BATCH`
2. **Database** — `SELECT id, file_name FROM documents WHERE file_hash = :h AND deleted_at IS NULL`
   → `DUPLICATE_DOCUMENT`

In-batch runs first so the response can name which file in *this* upload it collided with.

## 5. Level 2 — content hash

Runs inside the Celery task, immediately after extraction and normalisation:

```
extracted text
   ↓  normalise
   ├─ collapse all whitespace runs to a single space
   ├─ normalise line breaks and strip trailing spaces
   ├─ Unicode NFKC
   ├─ lowercase
   ├─ drop zero-width and control characters
   └─ trim
   ↓
SHA-256  →  documents.content_hash
   ↓
SELECT id FROM documents
 WHERE content_hash = :h AND id != :self
   AND deleted_at IS NULL AND status = 'COMPLETED'
```

Normalisation is what makes this useful: the same policy exported from Word to PDF has a
different `file_hash` and an identical `content_hash`. That is the case level 1 cannot catch and
level 2 exists for.

The comparison is restricted to `COMPLETED` documents so a half-processed row cannot be cited as
the original.

**On a match:**

```
status                    = DUPLICATE
duplicate_of_document_id  = <original>
current_stage             = 'duplicate_check'
storage blob              deleted  (DELETE_DUPLICATE_SOURCE_FILE, default true)
pipeline                  stops — no chunking, embedding or indexing
audit                     document.duplicate_detected
```

Stopping matters: indexing a duplicate puts the same passage in Qdrant twice, so retrieval
returns it twice and the model sees duplicated context. Keeping duplicates out of the index is
the whole point.

The row is kept so the admin sees *"Duplicate of Employee-Handbook-2024.docx"* rather than a
file that silently vanished.

## 6. Level 3 — semantic similarity

Off by default (`SEMANTIC_DUPLICATE_ENABLED=false`). When enabled, after embedding:

```
mean of the document's chunk vectors  →  document centroid
   ↓
Qdrant search against stored centroids, limit 5
   ↓
score >= SEMANTIC_DUPLICATE_THRESHOLD (0.95)
   ↓
is_possible_duplicate = true
duplicate_similarity  = score
```

**Processing continues normally.** The document is chunked, embedded and indexed. The flag is a
review affordance in the document list, nothing more.

The reason is concrete: at 0.90 similarity a document may genuinely differ — last year's edition
of the same policy scores very high against this year's. Auto-rejecting those destroys real
content.

## 7. Database impact

Reads `documents.file_hash` and `documents.content_hash`. Writes `content_hash`, `status`,
`duplicate_of_document_id`, `is_possible_duplicate`, `duplicate_similarity`. Inserts audit rows.

Indexes on `file_hash` and `content_hash` make both lookups a single index seek (§41).

## 8. API contract

Level 1, in the upload response:

```json
{ "file_name": "q3-report-copy.pdf", "status": "DUPLICATE",
  "error_code": "DUPLICATE_DOCUMENT",
  "duplicate_of": { "document_id": "…", "file_name": "q3-report.pdf" } }
```

Level 2, through the status endpoint:

```json
{ "document_id": "…", "status": "DUPLICATE", "current_stage": "duplicate_check",
  "duplicate_of": { "document_id": "…", "file_name": "employee-handbook-2024.docx" } }
```

`CONTENT_DUPLICATE` has **no HTTP status** — it can never be the outcome of a request.

## 9. Celery impact

Level 2 is a pipeline stage, immediately after normalisation and before classification — placed
there so a duplicate is caught *before* any AI cost is incurred.

## 10. Qdrant impact

Level 2 duplicates never enter the index. Level 3 reads the index; its documents remain indexed.

## 11. Redis impact

None.

## 12. Security considerations

- Hashes are of content, never of a filename, so renaming defeats nothing.
- The duplicate response reveals the **filename** of an existing document. Acceptable — there is
  one admin role, and the admin can list every document anyway.
- Hashes appear in logs; extracted text never does.

## 13. Error handling

Hashing failure → `PROCESSING_FAILED`. A duplicate is a **result**, not an error — no retry, no
alert.

## 14. Edge cases

| Case | Behaviour |
| ---- | --------- |
| Same file, two names | Duplicate at level 1 — hash is of content |
| Same text, PDF vs DOCX | Passes level 1, caught at level 2 |
| Same document, one page added | Passes 1 and 2; level 3 would flag it if enabled |
| Two identical files uploaded simultaneously in **separate requests** | Both may pass the level-1 check. Level 2 catches the second. See below |
| Matches a soft-deleted document | Accepted — deletion is not a permanent ban |
| Scanned copy of an existing digital document | OCR text rarely matches exactly; level 2 usually misses it, level 3 would catch it |
| Empty document | `NO_TEXT_EXTRACTED` before hashing |
| Original still processing | Not matched — comparison is restricted to `COMPLETED` |

> **The concurrent-upload race is known and accepted.** Two byte-identical files uploaded in the
> same instant can both pass level 1, because neither is committed when the other checks. Level 2
> resolves it: the second to finish extraction sees the first's `content_hash` and becomes
> `DUPLICATE`. A unique constraint on `file_hash` would close the race but would also block
> legitimate re-upload after deletion, so it is deliberately not used.

## 15. Testing requirements

Byte-identical re-upload · same file twice in one batch · same text in two formats · re-upload
after soft delete (must succeed) · concurrent identical uploads (level 2 must resolve) ·
normalisation equivalence (whitespace, case, line endings) · duplicate is not indexed ·
`duplicate_of` resolves · level 3 disabled by default · level 3 flags without rejecting.

## 16. Acceptance criteria

- [ ] Exact duplicates rejected at upload with the original named
- [ ] In-batch duplicates detected
- [ ] Content duplicates detected after extraction and marked `DUPLICATE`
- [ ] Content duplicates never appear in Qdrant
- [ ] `duplicate_of_document_id` set and resolvable in the UI
- [ ] Source blob deleted for content duplicates, row retained
- [ ] Semantic detection off by default; when on, flags only
- [ ] Re-upload after deletion succeeds
