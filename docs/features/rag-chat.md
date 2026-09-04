# Feature — RAG Chat

## 1. Requirement

A ChatGPT-style interface answering questions grounded strictly in the indexed corpus, with
source citations and explicit refusal when nothing supports an answer (§31–§35).

## 2. Business rules

- Answers use **only** retrieved context. Never the model's own knowledge (§34).
- When nothing is retrieved above threshold, the system says so. It does not answer.
- Every grounded answer carries citations (§35).
- Only `COMPLETED`, non-deleted documents are retrievable.
- The corpus is never sent to the model — only top-K chunks (§32).
- Citations are verified against what was actually retrieved.

## 3. User flow

```
/chat  →  sidebar of conversations, grouped Today / Yesterday / Previous 7 Days
       →  type a question  →  Send
       →  thinking state
       →  answer streams in, source chips beneath it
       →  click a chip → document detail at that page
```

Empty state: *"How can I help you today?"* — the Stitch markup already contains it, commented out.

## 4. Backend flow

```
POST /api/v1/chat/conversations/{id}/messages
  ├─ validate: non-empty, under MAX_MESSAGE_CHARS
  ├─ persist the user message
  ├─ load recent context from Redis
  ├─ AGENT 2 — rewrite query, propose filters        [8s, fallback: raw question]
  ├─ validate proposed filters against live taxonomy
  ├─ embed the query — 768-d, local, in a thread executor
  ├─ Qdrant search: top-K=5, score_threshold=0.35,
  │                 must(status = COMPLETED) + validated filters
  │
  ├─ no hits above threshold?
  │     └─ return the not-found answer. AGENT 3 IS NOT CALLED.
  │        is_grounded = false, no sources
  │
  ├─ AGENT 3 — compose grounded answer               [25s, fallback: templated]
  ├─ intersect cited ids with supplied ids — drop invented ones
  ├─ persist assistant message + message_sources
  ├─ update Redis context, update conversation
  └─ respond: answer + sources
```

Two steps carry most of the safety:

**Skipping agent 3 when retrieval is empty** removes the opportunity to answer unsupported. A
model handed zero chunks and asked to answer will often oblige.

**Intersecting citations** means the model does not get the final say on what it cited. An
invented `chunk_id` is dropped before the response is built.

## 5. Frontend flow

`/chat`, Next.js App Router. Message posts to a route handler which attaches the token
server-side. Optimistic render of the user's message; assistant message replaced when the
response lands.

800px max-width column, per `DESIGN.md`. Source chips render as
`[icon] Employee Handbook.pdf [Page 4]` — the Stitch markup for this already exists.

## 6. Database impact

Reads `conversations`. Writes `chat_messages` (user and assistant) and `message_sources`. Updates
`conversations.last_message_at` and `message_count`.

`message_sources.document_id` is `ON DELETE RESTRICT` — documents are soft-deleted, so a citation
in a year-old answer still resolves.

## 7. API contract

```json
POST /api/v1/chat/conversations/{id}/messages
{ "content": "What documents are required for employee onboarding?" }
```

```json
{ "success": true,
  "data": {
    "message_id": "…",
    "answer": "Based on the retrieved policies, the required documents are: …",
    "is_grounded": true,
    "sources": [
      { "document_id": "…", "document_name": "Employee Handbook.pdf",
        "chunk_id": "…", "page": 4, "score": 0.91, "rank": 1 },
      { "document_id": "…", "document_name": "HR Onboarding Policy.docx",
        "chunk_id": "…", "section": "Sec 2", "score": 0.87, "rank": 2 }
    ],
    "latency_ms": 3120
  } }
```

Not-found case — **HTTP 200, `success: true`**:

```json
{ "success": true,
  "data": { "answer": "I could not find this information in the available documents.",
            "is_grounded": false, "sources": [] } }
```

This is a correct outcome, not an error (§34). `NO_RELEVANT_CONTEXT` is the only code that
accompanies a successful response.

`answer` is **Markdown**. The composer chooses the shape from the question — a sentence for a
direct fact, prose for an explanation, a bulleted list for parallel items, a numbered list for an
ordered procedure, a table for a comparison, a fenced block for code or configuration. It is not
forced into one format, and a short question gets a short answer.

Additive rather than breaking: the field was always a string, and a plain sentence is valid
Markdown. Any client that renders it as text still works; the one shipped here renders it as
Markdown, with raw HTML escaped ([../frontend/screens.md](../frontend/screens.md) §6).

## 8. Celery impact

None. Chat is fully synchronous.

## 9. Qdrant impact

One filtered search per message. Read-only. Query semantics:
[../qdrant/collections.md](../qdrant/collections.md).

## 10. Redis impact

Recent context under `chat:{user_id}:{conversation_id}` — the last N turns, TTL 24 h. A cache
miss is harmless: PostgreSQL holds the permanent history and the context is rebuilt from it (§9,
§36).

## 11. Security considerations

- Retrieval filters on `status = COMPLETED` **inside** the Qdrant query, so an ineligible
  document never occupies a top-K slot.
- Authorisation is enforced in the application, never delegated to the model (§33).
- The Gemini key is server-side only.
- Prompt injection: a document instructing *"ignore previous instructions"* enters the context as
  data. The composer prompt states that context is reference material and never instruction. This
  reduces the risk; it does not eliminate it, and the honest position is that the corpus is
  admin-curated, which is the real mitigation.
- Message content is logged only at debug level, never in production.

## 12. Error handling

| Situation | Response |
| --------- | -------- |
| Empty message | 400 `MESSAGE_EMPTY` |
| Over length | 400 `MESSAGE_TOO_LONG` |
| Qdrant down | 503 `SEARCH_FAILED` |
| Agent 2 fails | Silent fallback to the raw query |
| Agent 3 fails | Silent fallback to a templated grounded answer |
| Agent 3 times out | 504 `AI_TIMEOUT` if the fallback also fails |
| Both Gemini and Qdrant down | 503 `AI_PROCESSING_FAILED` |
| Nothing retrieved | **200**, not-found answer |

## 13. Edge cases

| Case | Behaviour |
| ---- | --------- |
| Empty corpus | Not-found answer every time |
| Question in another language | Embedded as-is; `all-mpnet-base-v2` is English-centric, so quality drops |
| Question about a deleted document | Not retrieved. If cited in an old message, that citation still resolves |
| Very long question | Truncated to the encoder window before embedding |
| Follow-up with a pronoun | Agent 2 resolves it against Redis context |
| Same question twice | Same cost — no caching in v1 |
| All hits just below threshold | Not-found answer. **Deliberate** — a weak answer is worse than none |
| Document renamed after being cited | Old citations show the old name until the payload is updated |

## 14. Testing requirements

Relevant question returns a grounded answer with sources · irrelevant question returns not-found
with no sources · deleted document never retrieved · failed/duplicate documents never retrieved ·
citations always resolve to real chunks · invented citation ids are dropped · agent 2 failure
falls back cleanly · agent 3 failure falls back cleanly · Qdrant outage returns 503 · follow-up
resolves against context · answer never contains a fact absent from the supplied chunks.

That last one is the hard test and the important one. It needs a fixed corpus, a fixed question
set, and expected-answer assertions — a small evaluation set, not a unit test.

## 15. Acceptance criteria

- [x] Questions answered from indexed documents
- [x] Every grounded answer carries resolvable citations
- [x] Unsupported questions produce a refusal, not an answer
- [x] The whole corpus is never sent to the model
- [x] Deleted, failed and duplicate documents are unreachable
- [x] Conversation context resolves follow-ups
- [x] Agent failures degrade rather than error
- [x] History persists in PostgreSQL; Redis is only a cache
