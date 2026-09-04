# ADR-005 — Duplicate Detection Splits Across Two Moments

**Status:** Accepted · **Date:** 2026-08-21 · **Decides:** spec §18, §19, §20

## Context

§18 requires three levels of duplicate detection. §20 requires the upload endpoint to return
immediately without waiting for extraction or OCR.

These constraints collide. Level 1 (SHA-256) is a function of raw bytes and is available in
milliseconds. Levels 2 and 3 require *extracted text* — which is exactly what §20 forbids the
request from waiting for.

The collision is unavoidable, so detection is split across two moments.

## Decision

### Moment 1 — synchronous, inside `POST /api/v1/admin/documents/upload`

Filename sanitisation → extension → MIME → magic bytes → size → **SHA-256** → in-batch
comparison → database lookup. All cheap. Then persist, enqueue, respond.

Reports **exact (byte-identical) duplicates only**, as `DUPLICATE_DOCUMENT`.

### Moment 2 — asynchronous, inside the Celery task after extraction

Normalise text → **content hash** → compare against `documents.content_hash`.

A document accepted as `QUEUED` at upload **may later become `DUPLICATE`**. This is correct
behaviour, not a defect. `DUPLICATE` is already terminal in §21, so status polling stops there.

## Policy (approved)

| # | Question | Decision |
| - | -------- | -------- |
| a | Content duplicate found | **Stop the pipeline.** Never chunk, embed or index it |
| b | Database row | **Kept**, with `duplicate_of_document_id` self-FK to the original |
| c | Stored file | **Deleted**, controlled by `DELETE_DUPLICATE_SOURCE_FILE` (default `true`) |
| d | Level 3 semantic | **Implemented, disabled by default.** Never auto-rejects — sets `is_possible_duplicate` + score for admin review, per §18's own rule |

(a) matters most: indexing a duplicate puts the same passage in Qdrant twice, so retrieval
returns it twice and the model sees duplicated context. Keeping duplicates out of the index is
the entire purpose of the feature.

(b) means the admin sees *"Duplicate of Employee-Handbook-2024.docx"* rather than a file that
silently vanished, and gives the audit log something to reference.

## Alternatives rejected

- **Extract synchronously.** Violates §20. A 300-page scan means a 60–90 s HTTP request.
- **Extract synchronously for cheap formats only.** Identical files behave differently by type,
  and the async path is still required — two code paths for one feature.
- **SHA-256 only.** Loses the point: the same policy re-exported to PDF has a different
  SHA-256 and identical text. That is the case level 2 exists for.
