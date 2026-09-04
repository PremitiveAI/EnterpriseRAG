# AI — CrewAI Agents

Three agents, no more ([ADR-002](../architecture/decisions/ADR-002-crewai-three-agents.md)).
Adding a fourth requires a new ADR.

| # | Agent | Path | Blocking? |
| - | ----- | ---- | --------- |
| 1 | Identity Document Agent | Celery task, after OCR | No — async |
| 2 | Query Planner Agent | Chat request, before retrieval | **Yes** |
| 3 | Response Composer Agent | Chat request, after retrieval | **Yes** |

Agents 2 and 3 are on the synchronous chat path. Every message pays their latency, so both have
hard timeouts and **non-agent fallbacks**. A failed agent must never mean a failed chat.

## What is not an agent

§8's boundary, restated because it is the rule most easily eroded: file and MIME validation,
signature checks, hashing, duplicate comparison, text extraction, OCR invocation, normalisation,
language detection, chunking, embedding, vector upsert, status transitions, and all database
access are **ordinary services**. None of them may be routed through an agent.

---

## Agent 1 — Identity Document

**Purpose.** Recognise identity documents from an image and extract their fields — PAN card,
Aadhaar card, and others as the taxonomy grows.

**Runs when** the source is an image, or classification returns an identity type.

**Input.** Tesseract's OCR text plus image metadata. The agent does **not** perform OCR
([ADR-004](../architecture/decisions/ADR-004-tesseract-ocr.md)) — Tesseract produces the text,
the agent reasons over it. That division keeps §8 intact: recognition is reasoning, OCR is not.

**Output.**

```json
{
  "document_type": "pan_card",
  "confidence": 0.93,
  "fields": { "name": "…", "father_name": "…", "date_of_birth": "…", "pan_number": "…" },
  "notes": "…"
}
```

**Expect noisy input.** PAN and Aadhaar cards are dense, coloured, and usually photographed at an
angle — the hardest case for Tesseract. The prompt must therefore instruct the agent to work with
partial and garbled text, and to return `null` for a field rather than guess at one. A confidently
wrong Aadhaar number is far worse than a missing one.

**Validation before persistence.** Structural checks are deterministic and belong outside the
agent: PAN is `[A-Z]{5}[0-9]{4}[A-Z]`; Aadhaar is 12 digits and must pass the Verhoeff checksum.
A field failing its check is discarded, not stored.

> 🔴 **These fields are sensitive personal identifiers.** They are masked before any log or audit
> write, and full values are never sent to a log sink.
> [../security/pii-handling.md](../security/pii-handling.md)

**On failure.** Log, set `document_type = null`, continue. Classification and indexing proceed —
an unrecognised card is still a searchable document.

---

## Agent 2 — Query Planner

**Purpose.** Turn a conversational question into a retrieval query, and propose metadata filters.

**Input.** The raw question plus the last few turns from Redis.

**Why it earns its place.** Follow-up questions are unretrievable as written. *"What about for
contractors?"* embeds to nothing useful; resolved against the previous turn it becomes *"leave
policy entitlement for contractors"*, which retrieves correctly. That is genuine reasoning over
context, not string manipulation.

**Output.**

```json
{
  "search_query": "leave policy entitlement contractors",
  "filters": { "category_slug": "administrative-internal", "language": "en" },
  "intent": "policy_lookup"
}
```

**Filters are validated, not trusted.** A proposed `category_slug` is checked against the live
taxonomy and dropped if unknown. An invented filter that matches no document would silently
eliminate every result — the exact failure mode described in
[../qdrant/collections.md](../qdrant/collections.md).

| Guard | Value |
| ----- | ----- |
| Timeout | 8 s |
| Retries | 1 |
| **Fallback** | Retrieve on the **raw question**, no filters |

The fallback is not a degraded mode to avoid — for a well-formed standalone question it is
equivalent.

---

## Agent 3 — Response Composer

**Purpose.** Write a grounded answer from the retrieved chunks, with citations.

**Input.** The question and the top-K chunks with their payload. **Nothing else.** The corpus is
never sent (§32).

**Output.**

```json
{
  "answer": "…",
  "is_grounded": true,
  "cited_chunk_ids": ["…", "…"]
}
```

**Grounding rules**, from §34, stated to the agent as absolute:

> Use only the supplied context. If the answer is not supported by it, say the information was
> not found in the available documents. Do not invent facts, dates, policies, names or numbers.
> Do not cite anything not supplied. Prefer *"I could not find this information in the available
> documents"* over an unsupported answer.

**Citations are verified, not trusted.** `cited_chunk_ids` is intersected with the ids actually
supplied. An id the agent invented is dropped before the response is built — the model does not
get the final say on what it cited.

If retrieval returned nothing above threshold, agent 3 is **not called at all**. The system
returns the not-found answer directly, saving a call and removing any opportunity to answer
unsupported.

| Guard | Value |
| ----- | ----- |
| Timeout | 25 s |
| Retries | 1 |
| **Fallback** | A templated answer listing the retrieved passages and their sources |

The fallback is deliberately plain but still grounded and still cited — worse prose, same truth
guarantees.

### Answer formatting

`answer` is **Markdown**, and the agent chooses its shape to fit the question:

| Question | Shape |
| -------- | ----- |
| A direct factual question | One sentence. No heading, no bullet, no preamble |
| "Why", "what does this mean" | One or two short paragraphs |
| Several parallel facts | Bulleted list |
| A procedure, or anything ordered | Numbered list — numbers **only** when order matters |
| Two or more things across two or more attributes | Markdown table |
| Code, commands, queries, configuration | Fenced code block with a language tag |

The rules are stated as *"choose one shape that fits"*, never as a menu. A model handed a list of
available formats will use them, which is how every answer — including *"how many days of leave do
I get?"* — ends up as bullet points.

The templated fallback follows the same rule: one passage renders as a short paragraph, several as
a list. Bulleting a single excerpt is exactly the reflex these rules exist to prevent.

**The client renders this Markdown, so it is an injection surface.** Raw HTML is escaped rather
than parsed and link hrefs are restricted to http/https/mailto — see
[../frontend/screens.md](../frontend/screens.md) §6.

---

## Model configuration

| Item | Value |
| ---- | ----- |
| Provider | Google Gemini |
| Model | `GEMINI_MODEL` from `.env` — one variable, read once |
| Key | `GEMINI_API_KEY`, server-side only, never in the browser bundle (§40) |
| Temperature | 0.0 agents 1–2 · 0.2 agent 3 |
| Client | Constructed once at startup, not per call |

**One variable controls the model.** No module may hard-code a model id while a config variable
appears to control it — a pattern found in a sibling repo here, where the configured value was
silently ignored.

## Prompt configuration — dynamic rules

> **Designed, not built.** [ADR-009](../architecture/decisions/ADR-009-dynamic-agent-rules.md)
> · [agent-rules.md](agent-rules.md). Prompts are entirely in code today.

Agents 2 and 3 are planned to load part of their prompt from a file a Super Admin can edit,
under `backend/config/agent_rules/`. Agent 1 is out of scope.

A prompt is **not one editable thing**, and the split is the point of the design:

| Part | Editable | Why |
| ---- | -------- | --- |
| `ROLE`, `GOAL`, `BACKSTORY` | ✅ | Persona. Changing it changes tone and emphasis |
| Format guidance (`FORMAT_RULES`, `BRIEF_FORMAT_RULES`) | ✅ | Presentation |
| Grounding rules | ❌ | "Use ONLY the passages", "Do not invent facts" |
| Output contract | ❌ | The JSON shape every caller parses |

The last two are appended by code after the file's content, on every call, and are unreachable
from the editing page. Both exclusions exist because the failure is **silent**:

- Remove the JSON contract and agent 3 stops returning parseable output. `compose()` falls back
  to the templated answer and every chat quietly degrades — HTTP 200, no error code, nothing in
  the error log.
- Soften the grounding rules and the model answers from its own knowledge with
  `is_grounded: true`. The citation check drops invented *ids*; it cannot detect an invented
  *claim* attached to a real one.

The rule file sits **above** the fallbacks in the table below, not beside them. A missing,
empty or unreadable file falls back to the in-code prompt — the agent still runs normally and
never reaches its own fallback.

## Failure summary

| Agent | Failure | Result |
| ----- | ------- | ------ |
| 1 | Any | `document_type = null`, pipeline continues, document indexed |
| 2 | Timeout / error | Retrieve on the raw question |
| 3 | Timeout / error | Templated grounded answer |
| 3 | No context retrieved | Not called — not-found response, `is_grounded = false` |
| Any | Malformed JSON | Parsed leniently once, then treated as failure |

No agent failure produces a 500 on the chat path. `AI_PROCESSING_FAILED` is reserved for the case
where **both** the agent and its fallback are impossible — in practice, Gemini unreachable *and*
Qdrant unreachable.

## Cost

| Operation | Gemini calls |
| --------- | -----------: |
| Document, non-identity | 3 (classify, metadata, tags) |
| Document, identity | 4 |
| Chat message, context found | 2 (agents 2, 3) |
| Chat message, nothing found | 1 (agent 2 only) |

Nothing meters or caches this in v1. Every chat message costs two calls; there is no reason a
repeated question should, and caching is the first optimisation to reach for if spend matters.

---

## As built — Phase 6

| Agent | Implementation | Module |
| ----- | -------------- | ------ |
| 1 — Identity | Direct Gemini call | `app/modules/ai/identity_agent.py` |
| 2 — Query Planner | **CrewAI** | `app/modules/ai/query_planner.py` |
| 3 — Response Composer | **CrewAI** | `app/modules/ai/response_composer.py` |

Agents 2 and 3 share one entry point, `crew.run_json`, so the framework is configured in exactly
one place (`app/modules/ai/crew.py`). Agent 1 stays a direct call by decision — see ADR-002's
implementation note.

`crew.run_json` guarantees three things to its callers: it never raises, it never blocks past its
timeout, and it never logs a prompt. Every failure returns `None`, and the caller takes the
fallback documented above.

**Timeouts are enforced twice** — `Agent(max_execution_time=…)` inside CrewAI, and a future
timeout around the whole kickoff. The second exists because the first is advisory: a call stuck in
the HTTP layer can outlive it, and agents 2 and 3 sit on a synchronous request.

`GET /health` reports `agents.agents_enabled`. It is `false` when CrewAI is missing **or** when no
provider resolves — no active row, an unreadable database, or a credential that cannot be
decrypted ([ADR-010](../architecture/decisions/ADR-010-database-backed-llm-credentials.md)). Each
of those is the case where every answer silently comes from the templated fallback, so it is worth
checking before concluding the agents are working. The adjacent `llm` block says *which* of them
it is.

All three agents resolve their provider the same way, from the database. Agent 1 joined that path
in Release 2.1; before then it called the Gemini SDK directly, so a provider switch would have
reached two agents out of three.
