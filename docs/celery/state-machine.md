# Document State Machine

§21 requires explicit states and forbids arbitrary uncontrolled transitions.

## Two fields, not one

§21 lists coarse states (`QUEUED`, `PROCESSING`, `COMPLETED`) alongside stage-level ones
(`EXTRACTING`, `OCR`, `CHUNKING`) **and** a separate `current_stage` field. Those overlap.

Resolution:

| Field | Holds | Used by |
| ----- | ----- | ------- |
| `documents.status` | Coarse lifecycle state | List filters, retrieval eligibility, terminal checks |
| `document_processing.current_stage` | Fine-grained stage | Status endpoint, progress display, debugging |

A single enum would force a choice between answering *"which stage is it in"* and *"is it
finished"*. Two fields answer both, and the list query never needs a join to decide whether a
document is done.

## States

| Status | Terminal | Meaning |
| ------ | :------: | ------- |
| `UPLOADING` | | Request in flight — transient, rarely persisted |
| `VALIDATING` | | Server-side checks |
| `DUPLICATE_CHECK` | | Level-1 hash comparison |
| `QUEUED` | | Accepted, task enqueued. **The state the upload response returns** |
| `PROCESSING` | | Task picked up |
| `EXTRACTING` · `OCR` · `CLASSIFYING` · `CHUNKING` · `EMBEDDING` · `INDEXING` | | Active pipeline stages |
| `COMPLETED` | ✅ | Indexed and searchable |
| `FAILED` | ✅ | Pipeline error; `error_code` set |
| `DUPLICATE` | ✅ | Content duplicate; not indexed |
| `DELETED` | ✅ | Soft-deleted; vectors removed |

## Legal transitions

```
UPLOADING ──▶ VALIDATING ──▶ DUPLICATE_CHECK ──┬──▶ QUEUED ──▶ PROCESSING
                                               └──▶ DUPLICATE ✅   │
                                                                   ▼
                        ┌──────────────────────────────── EXTRACTING
                        │                                      │
                        │                          ┌───────────┴──────┐
                        │                          ▼                  │
                        │                         OCR ────────────────┤
                        │                                             ▼
                        │                                     (content duplicate?)
                        │                                       │          │
                        │                                       ▼          ▼
                        │                                  DUPLICATE ✅  CLASSIFYING
                        │                                                  │
                        │                                                  ▼
                        │                                              CHUNKING
                        │                                                  │
                        │                                                  ▼
                        │                                              EMBEDDING
                        │                                                  │
                        │                                                  ▼
                        │                                              INDEXING
                        │                                                  │
                        │                                                  ▼
                        └──────────────▶ FAILED ✅                    COMPLETED ✅

Re-entry:  COMPLETED | FAILED | DUPLICATE ──reprocess──▶ QUEUED
           any non-DELETED                ──delete─────▶ DELETED ✅
```

Rules:

1. **Any active state may go to `FAILED`.** Anything can break.
2. **`DELETED` is reachable from anywhere** except `DELETED` itself.
3. **Terminal states are re-enterable only via explicit admin action** — reprocess or delete.
4. Stages otherwise advance strictly forward. There is no path from `EMBEDDING` back to
   `EXTRACTING` inside one run.
5. `COMPLETED` requires `INDEXING` to have succeeded. A document is never "complete" while
   unsearchable.

## Enforcement

Transitions go through one function. Nothing assigns `status` directly.

```python
ALLOWED: dict[Status, set[Status]] = { ... }

def transition(session, document, to: Status, *, stage=None, error_code=None):
    if to not in ALLOWED[document.status]:
        raise IllegalTransition(f"{document.status} → {to}")
    ...
```

An illegal transition raises rather than silently correcting. A bug that would have quietly
resurrected a deleted document instead fails loudly in a test.

Every transition is committed immediately — the status endpoint is only useful if it reflects
reality within a second or two.

## Terminal states and polling

Clients stop polling at `COMPLETED`, `FAILED`, `DUPLICATE`, `DELETED` (§44). The API exposes
terminality explicitly so the client never hard-codes the list:

```json
{ "status": "COMPLETED", "is_terminal": true }
```

## Retrieval eligibility

Only `COMPLETED` documents with `deleted_at IS NULL` are retrievable. This is the whole of §33's
filtering under a single admin role
([ADR-001](../architecture/decisions/ADR-001-single-admin-role.md)) — and it is applied as a
Qdrant payload filter, not by post-filtering results, so an excluded document never occupies a
top-K slot.

## Progress mapping

`progress_percent` on the status endpoint is derived from stage position, not measured:

| Stage | % |
| ----- | -: |
| `QUEUED` | 0 |
| `PROCESSING` | 10 |
| `EXTRACTING` | 25 |
| `OCR` | 40 |
| `CLASSIFYING` | 55 |
| `CHUNKING` | 70 |
| `EMBEDDING` | 80 |
| `INDEXING` | 90 |
| terminal | 100 |

It is a UI affordance, not a metric. OCR on a 500-page scan sits at 40% for minutes — which is
why the UI shows the **stage label** ("Extracting text…", "Generating embeddings…") as the
primary signal, exactly as the Stitch dashboard does, with the bar as secondary.
