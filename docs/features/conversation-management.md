# Feature — Conversation Management

## 1. Requirement

Create, list, open and delete conversations. Recent context in Redis, permanent history in
PostgreSQL (§36).

## 2. Business rules

- **PostgreSQL is the permanent source of truth. Redis is only a cache** (§9).
- Losing Redis loses no history — context is rebuilt from PostgreSQL.
- Conversations are soft-deleted, so their messages' citations remain resolvable.
- Title is derived from the first user message; the admin may rename it.
- Sidebar grouping is by `last_message_at`: Today / Yesterday / Previous 7 Days / Older — exactly
  the grouping in the Stitch chat markup.

## 3. User flow

```
/chat
 ├─ "New Chat" → empty conversation, not persisted until the first message
 ├─ sidebar → search, date-grouped list
 ├─ select → messages and their source chips load
 └─ hover → rename · delete
```

A conversation is created lazily. Clicking New Chat repeatedly does not litter the sidebar with
empty rows.

## 4. Backend flow

### Create

```
POST /chat/conversations   { "title": null }
  → insert with a placeholder title, return the id
```

Title is replaced by a truncation of the first user message when that message arrives.

### List

```
GET /chat/conversations?search=&page=
  ├─ deleted_at IS NULL
  ├─ ORDER BY last_message_at DESC NULLS LAST
  └─ grouped client-side by date bucket
```

Served by the `(user_id, last_message_at DESC)` index — the sidebar query is the reason that
index exists.

### Detail

```
GET /chat/conversations/{id}
  → messages ordered by created_at, each with its message_sources
```

Sources are loaded with the messages in one query, not per message — otherwise a 40-message
conversation issues 40 extra queries to render its chips.

### Delete

```
DELETE /chat/conversations/{id}
  ├─ deleted_at = now()
  ├─ drop the Redis context key
  ├─ audit conversation.deleted
  └─ 200
```

Messages are **not** deleted. `message_sources` rows survive, so the audit trail of what was
cited remains intact.

## 5. Frontend flow

Sidebar is server-rendered and revalidated after each message. Selecting a conversation is a
route change to `/chat/{id}`, so conversations are linkable and the back button behaves. Delete
is optimistic with rollback on failure.

## 6. Database impact

`conversations` — insert, update `title`, `last_message_at`, `message_count`, `deleted_at`.
`chat_messages` and `message_sources` — read; written by the chat feature.

`message_count` is denormalised so the sidebar never counts rows.

## 7. API contract

```json
GET /chat/conversations
{ "success": true,
  "data": { "items": [
      { "id": "…", "title": "Employee Onboarding Docs",
        "message_count": 6, "last_message_at": "2026-08-21T09:40:00Z" }
    ], "page": 1, "page_size": 50, "total": 12 } }
```

## 8. Celery impact

None.

## 9. Qdrant impact

None.

## 10. Redis impact

```
key    chat:{user_id}:{conversation_id}
value  JSON list of the last N turns  (CHAT_CONTEXT_TURNS, default 6)
TTL    24 h
```

Written after each assistant message. Read by agent 2 before retrieval.

**A cache miss is not an error.** On miss, the last N turns are read from `chat_messages` and the
key is repopulated. The system is fully correct with Redis empty — only slower by one query.

That property is the point of §9's rule, and it is worth testing explicitly: flush Redis
mid-conversation and confirm the next follow-up still resolves.

Deleting a conversation drops its key immediately.

## 11. Security considerations

- All endpoints require authentication; conversations are scoped to `user_id` even though there
  is one admin, so the scoping is already correct if roles are ever added.
- Message content is **not** logged in production.
- Redis holds message text — it is on localhost, not exposed, and expires in 24 h. Worth knowing
  if the corpus is sensitive.
- Titles are escaped on render; a question containing markup cannot inject into the sidebar.

## 12. Error handling

`CONVERSATION_NOT_FOUND` 404 · `UNAUTHORIZED` 401 · `VALIDATION_ERROR` 422.

Redis unavailability is **not** an error — it degrades to a PostgreSQL read and is logged at
warning level.

## 13. Edge cases

| Case | Behaviour |
| ---- | --------- |
| New Chat clicked repeatedly | Nothing persisted until a message is sent |
| Conversation with no messages | Excluded from the sidebar |
| Very long first message | Title truncated to 60 chars on a word boundary |
| Redis flushed mid-conversation | Context rebuilt from PostgreSQL; follow-ups still resolve |
| Deleted then re-opened by URL | 404 |
| Message citing a since-deleted document | Citation still renders; the link shows the document as deleted |
| Conversation older than the TTL | Context rebuilt on next use |
| Two tabs on one conversation | Last write wins on the title; messages are append-only so both are kept |

## 14. Testing requirements

Lazy creation · title derived from first message · sidebar ordering and date grouping · soft
delete hides it and drops the Redis key · **context survives a Redis flush** · sources load with
messages in one query · deleted conversation returns 404 · rename persists.

## 15. Acceptance criteria

- [x] Conversations create lazily on first message
- [x] Sidebar lists them grouped by recency, most recent first
- [x] Opening one loads its messages and citations
- [x] Follow-up questions resolve using recent context
- [x] Flushing Redis loses no history and breaks no follow-up
- [x] Deletion is soft; citations survive
- [x] PostgreSQL alone is sufficient to reconstruct every conversation
