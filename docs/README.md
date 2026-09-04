# EnterpriseRAG — Documentation

Enterprise AI document management and RAG platform. FastAPI · Next.js · PostgreSQL · Qdrant ·
Redis · Celery · Gemini · CrewAI.

**All eight phases complete** (0–7) — discovery, documentation, foundation, upload, the
processing pipeline, document management, RAG chat, and testing and hardening. See [implementation-status.md](implementation-status.md) for what is built, what is
tested, and what is gated on a missing dependency.

`§n` references are to the master specification.

---

## Start here

| To understand… | Read |
| ---- | ---- |
| What the system is and how it fits together | [architecture/system-overview.md](architecture/system-overview.md) |
| **Every decision made and why** | [architecture/decisions/](architecture/decisions/) — 8 ADRs |
| The data model | [database/schema.md](database/schema.md) |
| The API surface | [api/overview.md](api/overview.md) |
| How to run it | [deployment/local-setup.md](deployment/local-setup.md) |
| What is built and what is not | [implementation-status.md](implementation-status.md) |

---

## Decisions (ADRs)

The eight approved answers that shape everything else. Read these before any implementation.

| ADR | Decision |
| --- | -------- |
| [001](architecture/decisions/ADR-001-single-admin-role.md) | Single administrator role — no per-document ACLs |
| [002](architecture/decisions/ADR-002-crewai-three-agents.md) | CrewAI with exactly three agents |
| [003](architecture/decisions/ADR-003-local-embeddings.md) | Local `all-mpnet-base-v2`, 768-d cosine |
| [004](architecture/decisions/ADR-004-tesseract-ocr.md) | Tesseract for OCR, conditional |
| [005](architecture/decisions/ADR-005-duplicate-detection-split.md) | Duplicate detection splits across two moments |
| [006](architecture/decisions/ADR-006-qdrant-collection-lifecycle.md) | Collection created once, never recreated |
| [007](architecture/decisions/ADR-007-native-windows-services.md) | Native Windows services, no Docker |
| [008](architecture/decisions/ADR-008-design-system-resolution.md) | Fully rounded · `#15157d` · EnterpriseRAG |
| [009](architecture/decisions/ADR-009-dynamic-agent-rules.md) | Agent prompts in files, with a locked grounding envelope |

---

## Architecture

| Document | Contents |
| -------- | -------- |
| [system-overview.md](architecture/system-overview.md) | Topology, the two request paths, where AI is and is not used, known constraints |
| [backend-architecture.md](architecture/backend-architecture.md) | Module layout, layer rules, request lifecycle, error envelope, configuration, storage abstraction |

## Database

| Document | Contents |
| -------- | -------- |
| [schema.md](database/schema.md) | 10 tables, columns, keys, indexes, ERD, the status-enum resolution |

## API

| Document | Contents |
| -------- | -------- |
| [overview.md](api/overview.md) | Auth, envelope, every endpoint, list parameters, polling, rate limits |
| [error-codes.md](api/error-codes.md) | Every `error_code`, its HTTP status and meaning |

## Features

Each follows the 15-section structure §14 requires.

| Document | Contents |
| -------- | -------- |
| [authentication.md](features/authentication.md) | Login, tokens, timing-safe failure, session handling |
| [document-upload.md](features/document-upload.md) | Validation order, partial batch success, per-file results |
| [duplicate-detection.md](features/duplicate-detection.md) | All three levels, the two-moment split, the accepted concurrency race |
| [document-processing.md](features/document-processing.md) | The 14-stage pipeline, per-stage failure and retry, idempotency |
| [document-management.md](features/document-management.md) | List, search, filter, edit, delete, reprocess |
| [rag-chat.md](features/rag-chat.md) | Retrieval, grounding, citations, refusal behaviour |
| [conversation-management.md](features/conversation-management.md) | Conversations, Redis context, PostgreSQL history |

## AI

| Document | Contents |
| -------- | -------- |
| [agents.md](ai/agents.md) | The three CrewAI agents, guards, timeouts, fallbacks, cost |
| [agent-rules.md](ai/agent-rules.md) | Super-Admin-editable agent prompts, file-based — **designed, not built** |

## Celery

| Document | Contents |
| -------- | -------- |
| [state-machine.md](celery/state-machine.md) | Legal transitions, terminal states, progress mapping |
| [tasks.md](celery/tasks.md) | Task definitions, retry policy, idempotency, Windows pool |

## Qdrant

| Document | Contents |
| -------- | -------- |
| [collections.md](qdrant/collections.md) | Collection config, payload schema, filters, deterministic point ids |

## Frontend

| Document | Contents |
| -------- | -------- |
| [design-system.md](frontend/design-system.md) | Tokens, the fully-rounded shape language, component inventory, accessibility |
| [screens.md](frontend/screens.md) | Screen by screen, including the five with no Stitch design |

## Security

| Document | Contents |
| -------- | -------- |
| [security-model.md](security/security-model.md) | Trust boundaries, upload surface, injection, operational exposure, accepted risks |
| [pii-handling.md](security/pii-handling.md) | PAN/Aadhaar masking, validation, storage, third-party transmission |

## Testing & Deployment

| Document | Contents |
| -------- | -------- |
| [testing/test-plan.md](testing/test-plan.md) | §46 coverage, fixtures, the grounding evaluation suite |
| [deployment/local-setup.md](deployment/local-setup.md) | Installs, the five-terminal runbook, verification, troubleshooting |

---

## Scope reminders

- **Phase 0 found a greenfield project.** `backend/`, `frontend/` and `docs/` were empty; the
  only inputs were three Stitch `code.html` files, three screenshots and `DESIGN.md`.
- **Five required screens have no design**: login, document management, document detail,
  settings, and upload results. They are derived from `DESIGN.md`, not from an approved comp —
  see [frontend/screens.md](frontend/screens.md).
- **Memurai's free edition prohibits production use.** Development-only until Linux, WSL2 or an
  Enterprise licence ([ADR-007](architecture/decisions/ADR-007-native-windows-services.md)).
- **Identity-document contents are transmitted to Google.** No redaction layer exists — see
  [security/pii-handling.md](security/pii-handling.md).
- No implementation begins until Phase 1 is approved (§55).

## Deferred to implementation phases

`architecture/frontend-architecture.md` and `architecture/data-flow.md` are covered in substance
by [system-overview.md](architecture/system-overview.md) and
[frontend/screens.md](frontend/screens.md); they will be split out if they grow. `api/` will gain
per-module request/response references as each module is built (§47).
