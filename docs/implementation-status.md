# Implementation Status

Updated 2026-08-21, after Phase 7. `§n` references are to the master specification.

| Phase | Scope | State |
| ----- | ----- | ----- |
| 0 | Read-only discovery | Complete |
| 1 | Documentation — 31 files, 8 ADRs | Complete |
| 2 | Foundation — config, logging, DB, migrations, auth, middleware | Complete |
| 3 | Upload — validation, hashing, storage, enqueue | Complete |
| 4 | Processing — extraction, OCR, chunking, embedding, indexing | Complete (with gates below) |
| 5 | Document management — list, search, filter, edit, delete, reprocess, download | Complete |
| 6 | RAG chat — retrieval, CrewAI agents 2 and 3, citations, conversations | Complete |
| 7 | Testing and hardening — rate limits, security headers, frontend tests | Complete |

---

## Tests

326 tests, plus 17 skipped (the opt-in grounding evaluation).

| Suite | Tests | Depends on |
| ----- | ----- | ---------- |
| `test_security.py` | 36 | — |
| `test_upload_validation.py` | 32 | — |
| `test_pipeline_units.py` | 44 | — |
| `test_auth_routes.py` | 10 | PostgreSQL |
| `test_upload_api.py` | 19 | PostgreSQL, local storage |
| `test_pipeline_e2e.py` | 18 | PostgreSQL, storage, Qdrant; 5 need Tesseract (~9 min) |
| `test_document_management_api.py` | 60 | PostgreSQL, local storage; 7 need Qdrant (~4 min) |
| `test_chat_units.py` | 31 | — |
| `test_chat_api.py` | 28 | PostgreSQL, Redis; 4 need Qdrant (~3.5 min) |
| `test_security_api.py` | 17 | PostgreSQL, Redis |
| `test_bundle_secrets.py` | 5 | `frontend/.next` (skips without a build) |
| `test_grounding_eval.py` | 17 | **Opt-in** — Gemini key, Qdrant; skipped by default |
| `test_ocr.py` | 11 | Tesseract binary (skips without it) |

The e2e suite runs the real pipeline end to end: `.txt` → COMPLETED, chunk traceability,
point-id parity between PostgreSQL and Qdrant, semantic retrieval, below-threshold refusal,
reprocessing without duplication or orphans, content-duplicate short-circuit, missing blob,
no-text, deleted-mid-flight, and the degraded-without-Gemini path.

---

## What is built but not exercised

These paths are implemented and unit-tested, but no test has run them against the real
dependency, because the dependency is absent on this machine.

**Tesseract is now installed** (5.5.3) and OCR is exercised live — see the Phase 7 addendum.

| Gate | Blocks | Behaviour today |
| ---- | ------ | --------------- |
| **`GEMINI_API_KEY` not set** | Title, description, category, tags, agent 1 (identity) | Stages degrade: title falls back to the filename, category and tags are left unset, `degraded: true` on the result. The document is still chunked, embedded and indexed |

The Gemini degradation path is covered by tests; the live path is not.

## ADR-002 — resolved in Phase 6

CrewAI **is installed** (1.15.17) and agents 2 and 3 run through it
(`app/modules/ai/crew.py`).

**Agent 1 deliberately stays a direct Gemini call.** It runs in the Celery task,
where the framework buys nothing — no delegation, no shared memory, no latency
budget to defend. Recorded in ADR-002's implementation note so the inconsistency
is a decision, not an oversight.

The upgrade this forced is recorded in `requirements.txt` and ADR-002: FastAPI
0.115.6 → 0.141.1 and pydantic 2.10.4 → 2.12.5, because `crewai` → `mcp` requires
`starlette>=1.6`. ~90 transitive packages came with it (chromadb, lancedb,
onnxruntime, pyarrow, kubernetes, openai), none of which this system uses.

---

## Phase 5 notes

Two behaviours worth knowing because they are decisions, not accidents.

**Editing is blocked while a document is moving.** Any non-terminal status — including
`QUEUED` — returns 409 `DOCUMENT_NOT_EDITABLE`. `QUEUED` counts as moving because a worker can
claim the row a millisecond later, and the pipeline writes AI-generated title, category and tags
over whatever the admin just saved.

**Delete writes to PostgreSQL before Qdrant.** If the vector removal then fails, the document is
already invisible to retrieval — `search()` filters on `status = COMPLETED` inside the query —
so the failure degrades safely. The response carries `vectors_removed: false` when that happens.
The reverse order would leave a window where the vectors are gone but the document still looks
live.

Source files are **retained** on delete (`DELETE_SOURCE_FILE_ON_DELETE = false`), so a soft
delete stays recoverable.

### Not built in Phase 5

**PII masking in the detail drawer.** [frontend/screens.md](frontend/screens.md) §5 and
[security/pii-handling.md](security/pii-handling.md) call for extracted identity fields to render
masked with an explicit reveal that writes a `document.pii_revealed` audit row. That is not
implemented, because there is nothing yet to mask: `identity_agent.analyse()` extracts and
validates PAN and Aadhaar fields, but the pipeline uses only `document_type` from the result and
**discards the fields** — they are never persisted. Storing them is a schema change and a
security decision, so it belongs to a phase that can make it deliberately, not to a list screen.

The `DOCUMENT_PII_REVEALED` audit action and the redaction logging filter already exist, so the
work is storage plus a masked renderer, not new infrastructure.

---

## Phase 6 notes

**The refusal is a success.** Nothing retrieved above threshold returns HTTP 200 with
`is_grounded: false`, empty `sources`, and `NO_RELEVANT_CONTEXT` inside the data. It is the only
error code that accompanies a successful response (§34).

**Agent 3 is not called when retrieval is empty.** Not "called and told to refuse" — not called.
A model handed zero passages and asked to answer will often oblige.

**Citations are intersected, not trusted.** `cited_chunk_ids` from the model is intersected with
the ids actually supplied, and then again against live `documents` rows, because a Qdrant payload
can outlive its row and `message_sources.document_id` is a foreign key. An answer that claims to
be grounded but cites nothing verifiable is replaced by the templated answer rather than shipped.

**Cleanup order is defined once, in `tests/conftest.py`.** `message_sources.document_id` is
ON DELETE RESTRICT, so a suite that deletes `documents` without first clearing the chat tables
raises `RestrictViolation`. Three suites had their own copy of the deletion list and all three
broke the moment chat tests started leaving citations behind. `reset_database()` is now the only
list.

**Both agents share one crew module.** `query_planner.crew` and `response_composer.crew` are the
same object, so a test that patches `crew.run_json` cannot tell which agent ran. Patch
`response_composer.compose`, or key the capture by `role`. Two tests were initially wrong this
way and passed for the wrong reason.

### Not built in Phase 6

**The grounding evaluation set.** [features/rag-chat.md](features/rag-chat.md) §14 calls the last
requirement — *"answer never contains a fact absent from the supplied chunks"* — the hard one, and
notes it needs a fixed corpus, a fixed question set and expected-answer assertions. That is an
evaluation harness, not a unit test, and it cannot be written honestly while `GEMINI_API_KEY` is
unset: every answer currently comes from the templated fallback, which is grounded by
construction. It belongs in Phase 7 with a key present.

**Streaming.** Answers arrive whole. The contract is already a complete snapshot, so streaming is
additive rather than a breaking change.

---

## Phase 7 notes

**Rate limiting now exists.** §40 specified four limits; until Phase 7 nothing enforced them —
only the `RATE_LIMIT_EXCEEDED` error code existed. `app/middlewares/rate_limit.py` is Redis-backed
and **fails open**: a Redis outage allows the request and logs a warning, because failing closed
would turn a cache outage into a total outage. That is a decision, and it is now recorded in
[security/security-model.md](security/security-model.md) rather than implied by the code.

**Rate limiting changed the test suite.** Once limits were real, suites that upload thirty files
in a second started failing with 429s that said nothing about the code under test. Counters are
now cleared by `reset_database()` alongside the tables — a test should not inherit another test's
budget.

**Two tests guard other tests.** `test_the_route_table_is_not_empty` exists because the
route-enumeration test reads the OpenAPI schema rather than `app.routes` — FastAPI 0.141 keeps
included routers as `_IncludedRouter` objects, so the naive walk finds four routes and passes
while checking almost nothing. `test_the_scan_actually_covers_something` exists because a
bundle-secret scan over zero files is a green tick meaning "no build exists".

**A test caught a flaw in the limiter itself.** `parse_rule("5")` originally defaulted to
`5/minute`. Silently reinterpreting a config value is exactly what that parser exists to prevent,
so a bare count is now rejected.

### Not built in Phase 7

**Playwright E2E.** The click-through — log in, upload, watch it index, ask a question, follow the
citation — is the one path no suite covers. API and component layers are both tested; the seam
between them is not.

**Coverage gating.** `pytest-cov` is installed but no run enforces the percentages in the test
plan. The targets remain aspirational.

**A committed binary fixture corpus.** Test files are byte literals in-test instead. What this
genuinely loses is the scanned-PDF and encrypted-PDF paths, which need real files.

---

## Addendum — Tesseract installed (2026-08-21)

Tesseract 5.5.3 is installed at `C:\Program Files\Tesseract-OCR`. It is **not on PATH** in this
environment, so `TESSERACT_CMD` in `backend/.env` points at the binary directly — which is the
documented fallback, and more robust than PATH in any case.

No application code changed. Everything was written in Phase 4 and waiting on the binary.

### What is now tested that could not be before

| Suite | Tests | Covers |
| ----- | ----: | ------ |
| `test_ocr.py` | 11 | Binary reachable, `eng` pack present, text and digits read back, blank image yields empty text rather than an error, corrupt bytes raise `OCR_FAILED` not `OCR_UNAVAILABLE`, per-page selection, 1-based page mapping, preprocessing output is binary single-channel |
| `test_pipeline_e2e.py` | 5 new | An image OCRed → chunked → embedded → indexed → **retrievable by meaning**; a scanned PDF keeping its page numbers; a text-layer PDF **never** rasterised; a blank scan failing with `NO_TEXT_EXTRACTED` rather than indexing nothing |

The last one matters most: empty OCR output must not become an indexed empty document, because it
would poison the content hash and match every future blank page (ADR-004).

Fixtures are **generated in-test**, never committed. A synthetic image is reproducible and
reviewable in the test that builds it, and for identity documents it removes any risk of a real
PAN or Aadhaar image entering the repository — which `docs/testing/test-plan.md` requires.

`test_a_digital_pdf_never_reaches_ocr` is the guard on the conditional: OCR is the slowest stage
in the pipeline, and a text-layer PDF that started being rasterised would be a silent, expensive
regression.

---

## Known intermittent — two Qdrant tests

`test_metadata_edit_updates_the_qdrant_payload` and
`test_reprocess_removes_old_vectors_before_requeueing` have each failed **once**, in different
runs. Recorded rather than dismissed, because an intermittent test is either a real race or a
test that lies, and neither should be discovered by someone else later.

What is known:

- Both touch Qdrant; both pass in isolation and in repeated normal runs (the 13 Qdrant-touching
  tests in `test_document_management_api.py` passed twice consecutively straight after).
- Both failures occurred in suite runs whose **wall-clock time was 12 hours and 24 hours** — the
  machine slept mid-run. Normal runs take ~15 minutes.
- The first failure was `category_slug` reading `None` where `hr-policies` was expected; the
  second was a vector count not reaching zero after a reprocess.

What is not known: whether a cached `QdrantClient` holding a dead keep-alive connection across
suspend/resume explains it. **No fix has been applied**, because a speculative change to
production code for an unreproducible failure is worse than an honest note.

If it recurs in a run that did **not** span a sleep, it is a real race and worth chasing then.

---

## Addendum — adaptive answer formatting (2026-08-23)

Answers were a single wall of text; now the composer picks a shape to fit the question and the
client renders it.

**Backend** — `app/modules/ai/response_composer.py` carries a `FORMAT_RULES` block: one sentence
for a direct fact, prose for an explanation, bullets for parallel items, a numbered list only when
order matters, a table for a real comparison, a fenced block for code or configuration. The rules
are phrased as *"choose one shape that fits"*, never as a menu — a model shown a list of available
formats uses them, which is how every answer becomes bullet points.

The templated fallback follows the same rule rather than exempting itself: one passage is a
paragraph, several are a list. It previously bulleted everything, including a single excerpt.

**Frontend** — `src/components/chat/Markdown.tsx`. The bubble rendered `whitespace-pre-wrap`
before, so a Markdown table would have appeared as raw pipes. Wide tables scroll inside their own
box rather than stretching the 800px column; headings render as `h3`/`h4` so an answer never
out-ranks the page heading. The user's own message stays plain text — someone typing `# hello`
meant a hash.

**This made the renderer an injection surface**, because the text comes from a model that has just
read admin-uploaded documents. `rehype-raw` is deliberately not used, so raw HTML is escaped and
shown as text, and link hrefs are filtered to http/https/mailto. Four tests assert it, including
that `<script>` and `javascript:` do not survive.

**Not asserted here:** that a live model actually obeys the format rules. A unit test can check
the instructions and the deterministic fallback; holding a model to its prompt belongs in the
grounding evaluation, which needs `GEMINI_API_KEY`.

Tests added: 10 backend (`TestResponseFormatting`), 15 frontend (`Markdown.test.tsx`).

---

## Addendum — three defects found from a live screenshot (2026-08-23)

A real answer in the UI showed the templated fallback, raw OCR noise, irrelevant passages, and a
PAN number in plain text. Three separate causes.

### 1. A doubled provider prefix disabled every agent

`.env` held `GEMINI_MODEL=gemini/gemini-3.6-flash`. The same value feeds two clients that want
different forms:

| Consumer | Builds | Result |
| -------- | ------ | ------ |
| CrewAI / LiteLLM | `f"gemini/{model}"` | `gemini/gemini/gemini-3.6-flash` — invalid |
| `google-generativeai` | `GenerativeModel(model)` | prefix makes the id invalid |

So agents 2 and 3 failed on **every** request and every answer silently came from the templated
fallback — which is exactly what a working fallback looks like from the outside. The bare id is
now stored on the write path and the prefix is composed in exactly one place.

`/health` reported `agents_enabled: true` throughout, because it only checked that a key was
present. It now reports an `llm` block carrying both the stored model and the composed
`resolved_model_id`, and `crew.probe()` makes one real call for when "configured" is not the same
question as "working".

> **Updated in Release 2.1.** The provider is no longer read from `.env` at all — it is a row in
> `llm_providers` ([ADR-010](architecture/decisions/ADR-010-database-backed-llm-credentials.md)),
> and `agents_enabled` now answers "does a provider resolve", not "is a variable set".

### 2. Agent 2's timeout was below the floor for a real call

A trivial CrewAI round-trip measures **~15s** here; the planner's timeout was 8s with one retry.
It therefore timed out on every request and spent ~16s doing so before using the fallback it
would have used anyway. Timeouts are now settings — 25s plan, 60s compose — and the planner does
not retry, because its fallback is equivalent for a well-formed question.

The frontend budget was 60s, below the backend's own worst case, so the BFF gave up while the
backend was still working: the user saw *"Could not reach the server."* for an answer that had
already been paid for. Now 150s, and the message distinguishes a **timeout** from a **refused
connection** — those need opposite fixes and looked identical before.

### 3. Identity numbers reached the screen unmasked

The answer displayed a PAN number, a name and a date of birth. `pii-handling.md` § Display
requires masking with an explicit reveal; that had only been applied to the document detail
screen, and the chat answer is a display surface that did not exist when it was written.

`app/utils/pii.py` masks at one choke point in `ChatService`, so the **stored** message and the
Redis context are masked too — masking only on the way out would leave the raw value in the
database and in the next follow-up's context.

It masks only values that **validate**: PAN by shape, Aadhaar by Verhoeff. A 12-digit invoice
number is not an Aadhaar, and mangling it into asterisks would make a correct answer look broken.
Over-masking has a real cost on a surface a person reads, which is why this is separate from the
blunter logging filter.

### Still open

Retrieval relevance. The screenshot shows a PAN-card question returning Leave Policy and
Maternity Act passages above the 0.35 threshold. With agent 2 disabled the raw question was
embedded as-is, so this may resolve itself now that the planner works — it needs re-measuring
before the threshold is touched.
