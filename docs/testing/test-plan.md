# Test Plan

§46. Tests are written alongside each phase, not deferred to Phase 7 — Phase 7 hardens and fills
gaps, it does not start from zero.

## Tooling

| Tier | Tools |
| ---- | ----- |
| Backend | `pytest`, `pytest-asyncio`, `httpx.AsyncClient` against the app, `pytest-cov` |
| Factories | `factory-boy` or plain fixtures |
| Frontend | `vitest` + `@testing-library/react` |
| E2E | Playwright — Phase 7 |
| CI | `pytest` + `npm run lint` + `npm run build` |

`npm run build` is a real gate: it type-checks the whole frontend and costs nothing extra.

## Test database and services

- PostgreSQL: a separate `enterprise_rag_test` database, migrated by Alembic, truncated between
  tests. Never the development database.
- Qdrant: a separate collection `documents_test`, dropped and recreated per session. This is the
  **only** place a destructive collection call is permitted
  ([ADR-006](../architecture/decisions/ADR-006-qdrant-collection-lifecycle.md)).
- Redis: a separate database index, flushed between tests.
- Celery: `task_always_eager = True` for most tests, so tasks run inline. A small set runs against
  a real worker to verify retry and idempotency, which eager mode cannot exercise.
- **Gemini and CrewAI are always mocked in CI.** Deterministic, free, and testable for failure —
  a real call cannot be made to time out on demand.

## Fixtures

A small corpus committed under `tests/fixtures/`:

| File | Purpose |
| ---- | ------- |
| `valid.pdf` | Text-layer PDF |
| `scanned.pdf` | Image-only — forces OCR |
| `valid.docx`, `valid.pptx`, `valid.txt` | Format coverage |
| `sample.png` | Image path |
| `corrupt.pdf` | Truncated — extraction failure |
| `encrypted.pdf` | Password-protected |
| `empty.txt` | Zero bytes |
| `renamed.pdf` | Actually a PNG — signature mismatch |
| `legacy.doc`, `legacy.ppt` | Must be rejected |
| `same-text.pdf` / `same-text.docx` | Content-duplicate pair |
| `pan-sample.png` | **Synthetic** identity card — never a real one |

The identity fixture is synthetic with invalid check digits. A real PAN or Aadhaar image must
never enter the repository.

## Upload (§46)

Valid file · invalid extension · invalid MIME · signature mismatch · oversized (both classes) ·
empty · malformed · multiple files · duplicate within batch · exact duplicate · re-upload after
soft delete (**must succeed**) · path-traversal filename · 255-byte filename · unicode filename ·
`.doc`/`.ppt` rejected · partial batch returns per-file results · **response completes well under
a second for a 20 MB PDF** · **no AI call occurs on the request path**.

The last two are the ones that catch a regression into synchronous processing.

## Processing

Each format end to end · scanned PDF triggers OCR · digital PDF skips it · OCR unavailable fails
with `OCR_UNAVAILABLE` rather than empty text · content duplicate stops **before** classification ·
classification failure still produces an indexed document · chunk boundaries respect the encoder
window · page numbers survive to chunks · language detected.

**Idempotency and retry** — the properties §23 demands, and the ones eager mode cannot test:

- Run `process_document` twice; chunk count and point count are unchanged
- Simulate a failure at the indexing stage, retry, verify no duplicate vectors
- Reprocess a document that yields fewer chunks; verify no orphan points remain
- Kill the worker mid-task; verify redelivery and convergence

## Documents

List · search matches title and filename · every filter individually and combined · sort
whitelist **rejects an arbitrary column name** · pagination boundaries and page beyond last ·
metadata edit updates the Qdrant payload · metadata edit does **not** change vectors · soft delete
hides from list and retrieval · vectors removed on delete · edit during processing returns 409 ·
download streams correctly · missing blob returns `STORAGE_FILE_MISSING` · every mutation writes
an audit row.

## RAG (§46)

Relevant question → grounded answer with resolvable sources · irrelevant question → not-found,
no sources, `is_grounded = false` · deleted document never retrieved · `FAILED` and `DUPLICATE`
never retrieved · **invented citation ids are dropped** · agent 2 failure falls back to the raw
query · agent 3 failure falls back to the templated answer · agent 3 **not called** when
retrieval is empty · Qdrant outage → 503 `SEARCH_FAILED` · follow-up resolves against context ·
**context survives a Redis flush** · metadata filters applied inside the query, not after.

### Grounding evaluation

The hardest requirement — *the answer contains no fact absent from the supplied chunks* — is not
a unit test. It needs a fixed corpus, a fixed question set and expected-answer assertions: a small
evaluation suite run deliberately, not on every commit.

Minimum: 10 questions with known answers, 5 with no supporting document. All 5 must produce a
refusal. A single hallucinated answer among them fails the suite.

## Security

Every route except `/health` and `/auth/login` returns 401 without a token — **enumerated from
the app's own route table**, so a newly added route cannot silently become public · wrong email
and wrong password are indistinguishable in body, status and timing · rate limits trip · path
traversal rejected · `sort` injection rejected · **no secret appears in the built frontend
bundle** · PII masking holds across every log sink including tracebacks · `audit_logs.metadata`
never matches a PII pattern.

The route-enumeration test is the one that would have caught the prefix-exemption bug described
in [../security/security-model.md](../security/security-model.md).

## Agent rules

> **Designed, not built.** [../ai/agent-rules.md](../ai/agent-rules.md) §14 ·
> [ADR-009](../architecture/decisions/ADR-009-dynamic-agent-rules.md).

Rules are files, so the suite writes to a **temporary rule directory**, never the real
`config/agent_rules/`. A test that edits the shipped prompt and fails before restoring it would
leave every later test — and the developer's next chat — running an unrelated persona. The same
mistake as a suite that shares a Qdrant instance with development.

**CRUD:** default returned when no rule is set · first save creates the file, so "add" and "edit"
are one call · a second save replaces · empty content restores the built-in prompt · `DELETE` is
405 because no handler exists · unknown agent key is 404 without touching the filesystem.

**Loading and fallback** — every row must leave the chat working:

| Condition | Expected |
| --------- | -------- |
| No file | In-code prompt, no warning |
| Empty / whitespace | In-code prompt |
| Unreadable (permissions) | In-code prompt + WARNING |
| Deleted between requests | Next request uses the in-code prompt |
| Oversized on save | 422, previous rule intact |
| Write fails | 500, previous rule intact — **not** truncated |

**The two that carry the design:**

- `test_an_edited_rule_is_actually_used_by_the_agent` — everything else can pass while the file is
  written, read and then ignored. Without this, the feature can be entirely inert and green.
- `test_grounding_rules_survive_a_hostile_edit` and the same for the JSON contract. Save a rule
  that tries to delete both, then assert the composed prompt still contains them. This is the
  security boundary of [../security/security-model.md](../security/security-model.md), asserted
  rather than described.

**Authorization:** Organization Admin is 403 on read and write · no token is 401. Both are already
covered generically by the route-enumeration test above, and are asserted explicitly here because
these routes edit global state.

**Regression:** the existing chat suites pass unchanged with no rule files present. That is the
proof that the feature is additive — the default path must be exactly what shipped.

## Frontend

`ui/` components render in every state · file validation matches server rules · per-file upload
states · polling stops at terminal states · empty vs no-results are distinct · chat not-found
renders as an answer, not an error · no token in `localStorage`, `sessionStorage` or any
client-visible variable.

## Coverage targets

| Area | Target |
| ---- | -----: |
| Services and repositories | 85% |
| Routes and controllers | 80% |
| Utils (hashing, normalisation, signatures) | 95% |
| Workers | 75% |
| Frontend components | 70% |

Percentages are a floor, not a goal. The `security` and `idempotency` groups matter more than any
number: they are the tests whose absence is not visible until something has already gone wrong.

## Out of scope for v1

Load and performance testing · penetration testing · full E2E across every browser ·
multi-language OCR accuracy · Gemini response-quality benchmarking beyond the grounding suite.

---

## As built — Phase 7

| Tier | State |
| ---- | ----- |
| Backend `pytest` | **300 passing**, 17 skipped (opt-in grounding eval) |
| Frontend `vitest` | **43 tests**, all passing |
| `npm run build` | Passing — type-checks the whole frontend |
| Grounding evaluation | Written, **opt-in**, currently skipped |
| Playwright E2E | **Not built** — see below |

### Running them

```bash
cd backend  && py -m pytest tests -q          # ~14 min: real PostgreSQL, Redis, Qdrant
cd frontend && npm test                       # ~10 s
```

The grounding evaluation is deliberately not part of either:

```bash
set RUN_GROUNDING_EVAL=1
py -m pytest tests/test_grounding_eval.py -v
```

It costs real Gemini calls, so it runs when someone means it. It also **skips itself when
`GEMINI_API_KEY` is unset** — without a key every answer comes from the templated fallback, which
is grounded by construction, so the suite would pass while proving nothing. That skip is the
honest outcome, not a workaround.

### Two guards on the guards

Two tests assert that other tests are actually testing something:

- `test_the_route_table_is_not_empty` — the route-enumeration test walks the OpenAPI schema, not
  `app.routes`. FastAPI 0.141 keeps included routers as `_IncludedRouter` objects, so the naive
  walk finds four routes and passes while checking almost nothing.
- `test_the_scan_actually_covers_something` — the bundle-secret scan over zero files would
  otherwise be a green tick meaning "no build exists".

### Deviations from the plan above

**No committed binary fixture corpus.** The `tests/fixtures/` table lists `valid.pdf`,
`scanned.pdf`, `encrypted.pdf` and so on. Test files are built from byte literals in-test
instead — the signature, size and format cases are covered, and the bytes are visible in the test
that uses them rather than in an opaque binary. What this genuinely loses is the **scanned and
encrypted PDF paths**: they need real files, and Tesseract is not installed to exercise the first
one anyway.

**No Playwright E2E.** The API and component layers are covered; what is untested is the
click-through: log in, upload, watch it index, ask a question, follow the citation. That is the
one gap where a failure would be invisible to every other suite.

**No coverage measurement.** `pytest-cov` is installed; no run enforces the percentages in the
table above. The targets remain aspirational rather than gated.
