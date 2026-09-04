# Feature — Document Processing Pipeline

Everything that happens after upload returns. One Celery task, twelve stages.

## 1. Requirement

Extract, OCR where needed, normalise, detect language, classify, extract metadata, generate tags,
chunk, embed and index — with per-stage error handling, logging, status updates, retry and
idempotency (§22).

## 2. The pipeline

```
process_document(document_id)
 │
 1  LOAD            fetch row + blob            → PROCESSING
 2  VALIDATE        blob exists, hash matches
 3  EXTRACT         per-format parser           → EXTRACTING
 4  OCR             only if needed              → OCR
 5  NORMALISE       whitespace, NFKC, case
 6  DUPLICATE       content hash                → DUPLICATE ⇢ stop
 7  LANGUAGE        detect ISO 639-1
 8  CLASSIFY        category + document_type    → CLASSIFYING
 9  METADATA        title, description
10  TAGS            keyword generation
11  CHUNK           ~1000 chars, 150 overlap    → CHUNKING
12  EMBED           768-d local encoder         → EMBEDDING
13  INDEX           upsert to Qdrant            → INDEXING
14  FINALISE        persist, timings            → COMPLETED
```

Stage 6 sits deliberately **before** stage 8 — a duplicate is caught before any AI cost.

Stage 2 re-verifies the hash because the blob may have changed or been corrupted between upload
and execution, particularly on a retry hours later.

## 3. Stage 3 — extraction

| Format | Library | Yields |
| ------ | ------- | ------ |
| PDF | PyMuPDF (`fitz`) | Text per page, page count |
| DOCX | `python-docx` | Paragraphs, tables, headings |
| PPTX | `python-pptx` | Slide text, notes; slide = page |
| TXT | stdlib | Encoding sniffed, UTF-8 fallback |
| JPG/PNG/WEBP | — | No text layer; goes straight to OCR |

`.doc` / `.ppt` never reach here — rejected at upload.

Extraction preserves **page or slide boundaries**. This is what makes `page_number` on a chunk —
and therefore the `Page 4` citation chip in the chat UI — possible. Losing it here cannot be
recovered later.

## 4. Stage 4 — OCR (conditional)

Runs when: the file is an image, **or** a PDF page yields fewer than `OCR_MIN_CHARS_PER_PAGE`
(default 50) characters.

```
page → rasterise at 300 DPI (PDF only)
     → greyscale → deskew → adaptive threshold   (OpenCV)
     → pytesseract.image_to_string(lang="eng")
```

Preprocessing is part of the service, not optional — it measurably changes accuracy on scans.

By far the slowest stage: seconds per page. This is the concrete reason §20 forbids extraction on
the request path. See [ADR-004](../architecture/decisions/ADR-004-tesseract-ocr.md).

If Tesseract is absent, the stage fails with `OCR_UNAVAILABLE` — it does not silently produce
empty text, because empty text is indistinguishable from a blank page and would poison the
content hash.

## 5. Stages 8–10 — the AI stages

| Stage | Input | Output | Guard |
| ----- | ----- | ------ | ----- |
| Classify | First ~4,000 chars + active taxonomy | Category **slug** + `document_type` | Must be one of the supplied slugs; unknown → `uncategorised` |
| Metadata | Same window | `title`, `description` | Length-capped, HTML-stripped |
| Tags | Same window | 3–10 tags | Lowercased, deduplicated, capped at 64 chars |

**Identity documents take a different path.** If the source is an image, or classification
returns an identity type, **CrewAI agent 1** runs over the OCR text and extracts the card type
and its fields. See [../ai/agents.md](../ai/agents.md).

Extracted PAN and Aadhaar values are **sensitive** and are masked before any log or audit write —
[../security/pii-handling.md](../security/pii-handling.md).

The classifier receives the taxonomy from the database (§24), never a hard-coded list.

## 6. Stage 11 — chunking

| Parameter | Default | Why |
| --------- | ------- | --- |
| `CHUNK_SIZE_CHARS` | 1000 | ≈250 word-pieces — safely under the encoder's 384 limit |
| `CHUNK_OVERLAP_CHARS` | 150 | Keeps a sentence spanning a boundary retrievable from either side |
| Split priority | paragraph → sentence → word | Never mid-word |

**The 384-token ceiling is the binding constraint.** `all-mpnet-base-v2` truncates beyond it
*silently* — no error, no warning, just a vector representing the first part of the text. A
chunk of 4,000 characters would embed roughly its first quarter and quietly lose the rest. 1,000
characters keeps every chunk inside the window with margin.
See [ADR-003](../architecture/decisions/ADR-003-local-embeddings.md).

Every chunk carries `document_id`, `chunk_index`, `page_number`, `section` and `language`, so it
traces back to its source (§26).

## 7. Stages 12–13 — embed and index

Chunks are embedded in batches of 32. The model is loaded once at worker startup, never per task.

Point ids are **deterministic**:

```
point_id = uuid5(NAMESPACE_DOCUMENT, f"{document_id}:{chunk_index}")
```

so a retry upserts over the same ids instead of doubling the chunks (§23). Before upserting, any
existing vectors for the document are deleted by payload filter — a reprocessed document that got
shorter cannot leave orphaned tail chunks searchable (§29).

Payload schema: [../qdrant/collections.md](../qdrant/collections.md).

## 8. Database impact

Writes `documents` (status, content_hash, category, language, title, description, page_count),
`document_processing` (stage, timings, retries, errors), `document_tags`, `document_chunks`, and
`audit_logs`.

Status is committed at **every** stage transition, not batched — the status endpoint is only
useful if it reflects reality within a second or two.

## 9. Failure handling per stage

| Stage | On failure | Retry? |
| ----- | ---------- | :----: |
| Load / validate | `STORAGE_FILE_MISSING` → `FAILED` | ❌ retrying cannot help |
| Extract | `EXTRACTION_FAILED` → `FAILED` | ❌ deterministic |
| OCR | `OCR_FAILED` / `OCR_UNAVAILABLE` | ✅ once — transient resource issues |
| Classify / metadata / tags | Log, **continue with defaults** | ✅ 2× |
| Chunk | `PROCESSING_FAILED` | ❌ deterministic |
| Embed | `EMBEDDING_FAILED` | ✅ 2× |
| Index | `VECTOR_INDEXING_FAILED` | ✅ 3× — Qdrant may simply be restarting |

The AI stages **degrade rather than fail**: a document that cannot be classified still gets
chunked, embedded and indexed, and is therefore still searchable. Losing a category label is a
metadata gap; losing the vectors would make the document invisible.

Deterministic failures are not retried — re-parsing a corrupt PDF three times only delays the
inevitable and burns worker time.

Retry policy in full: [../celery/tasks.md](../celery/tasks.md).

## 10. Idempotency

A retry must converge, not duplicate (§23):

- Deterministic point ids → upsert replaces
- Vectors deleted by `document_id` filter before re-indexing
- `document_chunks` rows deleted for the document before re-insert
- `document_tags` replaced, not appended
- Status transitions validated by the state machine

Running the task twice on the same document produces the same end state as running it once.

## 11. Observability

Per stage: `document_id`, `task_id`, `stage`, `duration_ms`, `status`, `retry_count` (§39).
Flower shows queue depth and failures. Never logged: extracted text, PII, file contents.

## 12. Edge cases

| Case | Behaviour |
| ---- | --------- |
| PDF with no text and OCR unavailable | `OCR_UNAVAILABLE` → `FAILED` |
| Document parses but yields nothing | `NO_TEXT_EXTRACTED` → `FAILED` |
| Password-protected PDF | `EXTRACTION_FAILED` |
| 500-page scan | Works; minutes long. `--pool=solo` blocks other documents meanwhile |
| Document deleted mid-processing | Task detects `deleted_at`, aborts, indexes nothing |
| Qdrant down at stage 13 | Retries 3×, then `VECTOR_INDEXING_FAILED`; text and metadata survive, so reprocess resumes cheaply |
| Worker killed mid-task | Task redelivered; idempotency makes the rerun safe |
| Mixed-language document | Dominant language detected; chunks inherit it |

## 13. Testing requirements

Each format end to end · scanned PDF triggers OCR · digital PDF skips it · duplicate stops before
classification · classification failure still yields an indexed document · retry produces no
duplicate vectors · reprocessing a shortened document leaves no orphan chunks · chunk count and
page numbers correct · deletion mid-flight aborts cleanly · Qdrant outage retries then fails
gracefully.

## 14. Acceptance criteria

- [ ] All six supported formats process to `COMPLETED`
- [ ] OCR runs only when needed
- [ ] Every stage transition is persisted and visible via the status endpoint
- [ ] AI-stage failures degrade to defaults without failing the document
- [ ] Retries never duplicate vectors or chunks
- [ ] Every chunk traces to document, page and index
- [ ] Content duplicates stop before any AI call
- [ ] No PII or document text appears in any log
