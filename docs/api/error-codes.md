# Error Codes

Every failure carries a stable, machine-readable `error_code` (§38). Clients branch on the code,
never on the message text — messages are for humans and may be reworded.

## Envelope

```json
{
  "success": false,
  "error_code": "FILE_TOO_LARGE",
  "message": "File exceeds the 20 MB limit for documents.",
  "details": { "file_name": "scan.pdf", "size_bytes": 25690112, "limit_bytes": 20971520 },
  "request_id": "01J8…"
}
```

`details` is optional and always structured. It **must never** contain file contents, extracted
text, PII or secrets (§39).

## Authentication

| Code | HTTP | Meaning |
| ---- | :--: | ------- |
| `UNAUTHORIZED` | 401 | Missing, malformed or expired token |
| `INVALID_CREDENTIALS` | 401 | Wrong email or password — **deliberately identical for both**, so the response cannot enumerate accounts |
| `ACCOUNT_INACTIVE` | 403 | `is_active = false` |
| `TOKEN_EXPIRED` | 401 | Access token expired; the client should refresh |
| `REFRESH_TOKEN_INVALID` | 401 | Refresh revoked or unknown — re-login |
| `FORBIDDEN` | 403 | Reserved. Unused while there is one role |

## Upload and validation

| Code | HTTP | Meaning |
| ---- | :--: | ------- |
| `INVALID_FILE_TYPE` | 400 | Extension not in the allow-list |
| `LEGACY_FORMAT_UNSUPPORTED` | 400 | `.doc` / `.ppt` — rejected by design ([ADR](../architecture/decisions/ADR-005-duplicate-detection-split.md), §15) |
| `INVALID_MIME_TYPE` | 400 | Server-derived MIME not permitted |
| `FILE_SIGNATURE_MISMATCH` | 400 | Magic bytes contradict the extension — a `.exe` renamed `.pdf` |
| `FILE_TOO_LARGE` | 413 | Over the limit for its class |
| `FILE_EMPTY` | 400 | Zero bytes |
| `INVALID_FILE` | 400 | Malformed or unreadable |
| `INVALID_FILENAME` | 400 | Path traversal, control characters, or over 255 bytes |
| `NO_FILES_PROVIDED` | 400 | Empty request |
| `TOO_MANY_FILES` | 400 | Over `MAX_FILES_PER_BATCH` |

## Duplicates

| Code | HTTP | When |
| ---- | :--: | ---- |
| `DUPLICATE_DOCUMENT` | 409 | Exact SHA-256 match — **synchronous**, at upload |
| `DUPLICATE_IN_BATCH` | 409 | Two files in the same request are byte-identical |
| `CONTENT_DUPLICATE` | — | Identical normalised text. **Asynchronous** — never an HTTP error; surfaces as `status = DUPLICATE` |

`CONTENT_DUPLICATE` has no HTTP status because it cannot be detected during the request. See
[ADR-005](../architecture/decisions/ADR-005-duplicate-detection-split.md).

## Documents

| Code | HTTP | Meaning |
| ---- | :--: | ------- |
| `DOCUMENT_NOT_FOUND` | 404 | Unknown id, or soft-deleted |
| `DOCUMENT_ALREADY_DELETED` | 409 | Delete on a soft-deleted document |
| `DOCUMENT_NOT_EDITABLE` | 409 | Metadata edit while processing |
| `DOCUMENT_NOT_REPROCESSABLE` | 409 | Reprocess while already in flight |
| `STORAGE_FILE_MISSING` | 500 | Row exists, blob does not — storage drift |

## Agent rules (Super Admin)

> **Designed, not built.** [ai/agent-rules.md](../ai/agent-rules.md) §12 ·
> [ADR-009](../architecture/decisions/ADR-009-dynamic-agent-rules.md).

| Code | HTTP | Meaning |
| ---- | :--: | ------- |
| `AGENT_NOT_FOUND` | 404 | Unknown agent key. Not a filesystem lookup — the key is not in the registry |
| `AGENT_RULE_INVALID` | 422 | Too large, or contains control characters |
| `AGENT_RULE_WRITE_FAILED` | 500 | The file could not be written; the previous rule is still in effect |

A **read** failure has no code, and that is deliberate. A missing, empty or unreadable rule file
is not an error the caller sees: the agent uses its in-code prompt and the chat proceeds. It is
logged at WARNING, because a rule that silently stopped applying is indistinguishable from one
that was never edited.

`AGENT_RULE_WRITE_FAILED` is a 500 that is safe to retry: the write is atomic — temp file, then
replace — so a failure leaves the old rule intact rather than a truncated one. A truncated prompt
would still be a *valid* prompt, and would be used.

## Processing (recorded on the document, not returned to a client)

| Code | Meaning |
| ---- | ------- |
| `PROCESSING_FAILED` | Unclassified pipeline failure |
| `EXTRACTION_FAILED` | Parser could not read the file |
| `OCR_FAILED` | Tesseract missing or errored |
| `OCR_UNAVAILABLE` | Binary not found on `PATH` |
| `CLASSIFICATION_FAILED` | Agent or Gemini failure during classification |
| `EMBEDDING_FAILED` | Encoder error |
| `VECTOR_INDEXING_FAILED` | Qdrant unreachable or rejected the upsert |
| `NO_TEXT_EXTRACTED` | Parsed successfully, produced nothing — an image-only PDF with OCR disabled |
| `RETRY_LIMIT_EXCEEDED` | Exhausted `max_retries` |

These are written to `document_processing.error_code` and surfaced through the status endpoint.

## Chat and retrieval

| Code | HTTP | Meaning |
| ---- | :--: | ------- |
| `CONVERSATION_NOT_FOUND` | 404 | |
| `MESSAGE_EMPTY` | 400 | Blank or whitespace-only |
| `MESSAGE_TOO_LONG` | 400 | Over `MAX_MESSAGE_CHARS` |
| `SEARCH_FAILED` | 503 | Qdrant unreachable |
| `AI_PROCESSING_FAILED` | 503 | Gemini or agent failure with no usable fallback |
| `AI_TIMEOUT` | 504 | Agent exceeded its timeout |
| `NO_RELEVANT_CONTEXT` | **200** | Not an error — retrieval found nothing above threshold |

`NO_RELEVANT_CONTEXT` is the one case that returns 200 with `success: true`. It is the correct
outcome of §34: the system answers *"I could not find this information in the available
documents."* rather than inventing one. It is recorded as `is_grounded = false` on the message.

## LLM provider configuration (ADR-010)

| Code | HTTP | Meaning |
| ---- | :--: | ------- |
| `LLM_NO_ACTIVE_PROVIDER` | 503 | No provider row is active |
| `LLM_CONFIG_UNAVAILABLE` | 503 | The active configuration could not be read or decrypted |
| `LLM_PROVIDER_NOT_FOUND` | 404 | No provider with that id |
| `LLM_PROVIDER_TEST_FAILED` | 422 | The pre-flight call failed. **Nothing was written** |
| `LLM_PROVIDER_CHANGED` | 409 | The credential changed between the test and the commit |
| `LLM_PROVIDER_ACTIVE` | 409 | Deleting the provider that is currently answering |

`LLM_PROVIDER_TEST_FAILED` is 422 rather than 502 because from the caller's point of view the
*submission* is what failed — these credentials, this model, this endpoint. The provider's own
error text is logged and never returned: it can echo the request, and the request carries the key.

`LLM_PROVIDER_CHANGED` covers two situations with one meaning. Either another Super Admin edited
the row while it was being verified, or two activations raced and the unique index refused one.
In both, what was tested is not what would have gone live.

Both are 503 and both mean no answer is possible, but they point at different fixes: the first is
a Super Admin who has not chosen a provider, the second is a database that cannot be read — or a
credential that cannot be decrypted. Rule 1 is why they are two codes and not one.

Neither ever describes the state of the key registry. A caller learns that the configuration is
unavailable, never which key is missing.

`LLM_CONFIG_UNAVAILABLE` is raised only after last-known-good is exhausted: a process that has
never read a configuration, or a cached one older than `LLM_CONFIG_STALENESS_SECONDS`. Inside that
window the request succeeds and carries `degraded: true` instead.

## Infrastructure

| Code | HTTP | Meaning |
| ---- | :--: | ------- |
| `RATE_LIMIT_EXCEEDED` | 429 | With `Retry-After` |
| `SERVICE_UNAVAILABLE` | 503 | A dependency is down |
| `INTERNAL_ERROR` | 500 | Unhandled — logged with full trace server-side, **never returned** |

## Rules

1. One code per distinct failure. Do not reuse a code for two causes.
2. Codes are `SCREAMING_SNAKE_CASE` and permanent — adding is safe, renaming is breaking.
3. The HTTP status and the code always agree.
4. `INTERNAL_ERROR` is a bug, not a category. Anything recurring earns its own code.
5. Messages never include a path, a stack frame, an SQL fragment or a dependency version.
