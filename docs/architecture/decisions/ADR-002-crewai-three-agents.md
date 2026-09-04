# ADR-002 — CrewAI With Three Agents

**Status:** Accepted · **Date:** 2026-08-21 · **Decides:** spec §7 vs §8

## Context

§7 lists CrewAI in the stack; §8 warns against introducing it without genuine multi-agent value.
Phase 0 recommended omitting it. **That recommendation was overruled**, with three specific
agents named — each of which does involve reasoning rather than deterministic work, so §8 is
satisfied on the merits.

## Decision

Use CrewAI for exactly **three** agents. No others without a new ADR.

| # | Agent | Runs in | Input | Output |
| - | ----- | ------- | ----- | ------ |
| 1 | **Identity Document Agent** | Celery task, after OCR | OCR text + image metadata | Document type (PAN card, Aadhaar card, …) and extracted fields |
| 2 | **Query Planner Agent** | Chat request, before retrieval | Raw user question + recent context | Rewritten search query + metadata filters |
| 3 | **Response Composer Agent** | Chat request, after retrieval | Retrieved chunks + question | Grounded answer + source attributions |

## Boundaries — what stays deterministic

Per §8, these remain ordinary services and must **never** be routed through an agent: file and
MIME validation, signature checking, hashing, text extraction, OCR invocation, normalisation,
chunking, embedding, vector upsert, status transitions, database access.

Agent 1 does **not** perform OCR. Tesseract produces the text; agent 1 reasons over it
(see [ADR-004](ADR-004-tesseract-ocr.md)).

## Consequences

- CrewAI and its transitive dependencies enter `requirements.txt`. It pins its own LLM client
  stack, so version conflicts with the Gemini SDK must be checked at install time.
- Agents 2 and 3 sit on the **synchronous** chat request path and add latency to every message.
  Both need explicit timeouts and a non-agent fallback: if agent 2 fails, retrieve on the raw
  question; if agent 3 fails, return a plain templated answer over the same chunks. A failed
  agent must never mean a failed chat.
- Agent 1 sits on the async path, so its latency is invisible to the user.
- Agent 1 handles **PAN and Aadhaar numbers** — sensitive personal identifiers. See
  [security/pii-handling.md](../../security/pii-handling.md); these must never reach logs.

## Alternatives rejected

**Single Gemini call per job, no framework.** Would work and would be lighter, but the user
explicitly requires a multi-agent structure. Recorded so the trade-off is not re-litigated.

---

## Implementation note — 2026-08-21, Phase 6

All three agents are wired. Agents 2 and 3 run through CrewAI
(`app/modules/ai/crew.py`, one `run_json` entry point shared by both).

**Agent 1 remains a direct Gemini call**, not a CrewAI agent. It runs inside the
Celery task, where the framework buys nothing: there is no delegation, no shared
crew memory and no latency budget to defend. Routing it through CrewAI would add
a dependency to the worker for a single JSON extraction. Recorded here so the
inconsistency is a decision rather than an oversight.

### What installing CrewAI actually cost

This ADR predicted "version conflicts with the Gemini SDK must be checked at
install time". The conflict was elsewhere, and larger:

- `crewai` depends on `mcp`, which requires `starlette>=1.6`. FastAPI 0.115.6
  pinned `starlette<0.42`, so **FastAPI was upgraded 0.115.6 → 0.141.1** and
  **pydantic 2.10.4 → 2.12.5** to accommodate it.
- ~90 transitive packages arrived, including **chromadb, lancedb, onnxruntime,
  pyarrow, kubernetes and openai** — a second vector database and a second
  embedding runtime, none of which this system uses. Retrieval is Qdrant;
  embeddings are local sentence-transformers (ADR-003).
- `crewai-core` is **not** a lighter alternative. It contains settings,
  telemetry and path helpers; it has no `Agent`, `Task` or `Crew`.

The full test suite was re-run against the upgraded stack. The alternative —
keeping the three-agent structure but calling Gemini directly — was offered and
declined; the framework was a stated requirement.

### Amended 2026-09-02, Release 2.1 — agent 1 joins CrewAI

The note above states that **agent 1 remains a direct Gemini call**, and gives a
sound reason: in the Celery worker the framework buys nothing. That reasoning
was correct and is now outweighed.

[ADR-010](ADR-010-database-backed-llm-credentials.md) moves the provider into
the database so a Super Admin can switch it without a restart. A switch reaches
whatever resolves its provider through the shared path — and agent 1, calling
`google-generativeai` directly with `settings.GEMINI_API_KEY`, did not. Leaving
it there would have meant an activation that silently applied to **two agents
out of three**, with the third answering from a provider nobody selected and
nothing anywhere reporting the difference.

So agent 1 now runs through `crew.run_json` like agents 2 and 3, with its own
role, goal and backstory (`identity_agent.ROLE` and friends), temperature 0.0
and a 45-second timeout — generous, because nothing waits on it.

What this costs, recorded so it is not rediscovered: the Celery worker now
imports CrewAI on the identity path. That dependency was already installed for
the API, and the worker already imported the module transitively, so the cost is
import time in a process that is not latency-sensitive.

`gemini_client` is superseded by `llm_client`, which is provider-agnostic and
resolves from the database. The three enrichment stages — classification,
tagging, title — move with it. They are **not** agents and do not become any:
the roster stays at three, and this ADR still governs adding a fourth.

### Failure behaviour is unchanged

`crew.run_json` never raises and never blocks past its timeout. Every failure
returns `None` and the caller uses its documented fallback: agent 2 falls back
to the raw question, agent 3 to a templated grounded answer. `/health` reports
`agents.agents_enabled`, so an agent silently running on its fallback because
no API key is set is visible rather than invisible.
