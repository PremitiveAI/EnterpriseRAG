# PII Handling

Agent 1 extracts fields from identity documents — PAN cards, Aadhaar cards
([ADR-002](../architecture/decisions/ADR-002-crewai-three-agents.md)). Those fields are sensitive
personal identifiers, and §39 forbids logging sensitive personal data.

This document defines what "handled correctly" means concretely.

## What counts as PII here

| Field | Sensitivity | Example format |
| ----- | ----------- | -------------- |
| Aadhaar number | **Highest** — a national identity number | 12 digits |
| PAN number | **High** — a tax identifier | `ABCDE1234F` |
| Date of birth | High in combination | |
| Full name, father's name | Moderate | |
| Address | Moderate | |
| Photograph / signature | High — biometric-adjacent | Image region |

Aadhaar is the one to be strictest with: it is a national identity number, and Indian regulation
around its storage and display is specific and stricter than for most identifiers.

## Rules

### 1. Never log a full identifier

```python
def mask_identifier(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    return "*" * max(0, len(value) - keep) + value[-keep:]

# 123456789012  →  ********9012
# ABCDE1234F    →  ******234F
```

Applied by a **logging filter**, not by call sites. Relying on every developer to remember to
mask fails the first time someone adds a debug line. The filter runs on every record and redacts
by pattern regardless of how the value arrived.

Patterns matched: 12 consecutive digits, `[A-Z]{5}[0-9]{4}[A-Z]`, and any value under a key named
`pan_number`, `aadhaar_number`, `aadhar_number`, `date_of_birth`, `dob`.

### 2. Never put PII in `audit_logs.metadata`

Audit rows reference the **document id**, never its extracted contents. `metadata` may say
`{"document_type": "pan_card"}`. It may not say which number.

### 3. Never put PII in an error message

`processing_error` is operator-facing and read through the API. `"Failed to parse field near
ABCDE1234F"` leaks through the status endpoint into the browser.

### 4. Never put PII in the Qdrant payload beyond the chunk text

The payload carries `text`, which for an identity document *is* the card contents. That is
unavoidable — it is what makes the document retrievable. But no **additional** structured PII
field is added to the payload.

### 5. Validate structurally, then discard failures

Validation is deterministic and belongs outside the agent:

| Field | Check |
| ----- | ----- |
| PAN | `^[A-Z]{5}[0-9]{4}[A-Z]$` |
| Aadhaar | 12 digits **and** Verhoeff checksum |
| DOB | Parses to a plausible date |

A field failing its check is **discarded, not stored**. A confidently wrong Aadhaar number is
worse than a missing one — it looks authoritative and will be trusted.

## Storage

| Location | Holds PII? | Notes |
| -------- | :--------: | ----- |
| `documents.title` / `description` | Possibly | AI-generated; may include a name |
| `document_chunks.text` | **Yes** | The card contents. Unavoidable — required for retrieval |
| Qdrant payload `text` | **Yes** | Same text |
| `document_processing.processing_error` | **No** | Enforced |
| `audit_logs.metadata` | **No** | Enforced |
| `logs/*.log` | **No** | Enforced by the logging filter |
| Redis chat context | Possibly | If an answer quoted a card. TTL 24 h |
| Storage blob | **Yes** | The original scan |

Two places hold PII because the product requires it — the chunk text and the source file. The
control there is access, not redaction: both are reachable only by an authenticated admin.

## Third-party transmission

**Identity document contents are sent to Google.** OCR text goes to Gemini via agent 1 for
recognition and extraction; chunk text goes to Gemini via agent 3 when an answer cites a card.

There is no redaction layer and no flag to disable it. Stated plainly because it is the fact most
likely to matter to a compliance review, and the one easiest to discover too late.

Anyone with data-residency, biometric-data or Aadhaar-specific obligations must evaluate this
**before** ingesting identity documents.

## Retention and deletion

- Soft delete removes vectors immediately, so the content stops being retrievable at once.
- `purge_deleted_documents` deletes the source blob after `RETENTION_DAYS`.
- `document_chunks` rows for a deleted document should be purged on the same schedule — they hold
  the card text and serve no purpose once the document is gone.
- The `documents` row is retained permanently so historical citations resolve. It holds metadata,
  not card contents.

## Display

The document detail screen shows extracted identity fields **masked by default**, with an
explicit "reveal" action. Revealing writes an audit row. The admin can see the original file
regardless, so this is not a security boundary — it prevents casual shoulder-surfing and creates
a record of deliberate access.

## Testing requirements

- Logging filter masks Aadhaar and PAN in every log sink, including tracebacks
- A deliberately logged raw identifier does not appear in `logs/`
- `processing_error` never contains a matched pattern
- `audit_logs.metadata` never contains a matched pattern
- Invalid PAN and Aadhaar values are discarded, not stored
- Verhoeff validation rejects a transposed-digit Aadhaar
- Deleting a document purges its chunk text on schedule
- Detail screen masks by default; reveal writes an audit row

## Known gaps

1. **No redaction before sending to Gemini.** Accepted; recorded.
2. **Chunk text stores card contents in plaintext.** Encryption at rest for
   `document_chunks.text` is not implemented.
3. **No consent or lawful-basis tracking.** Out of scope, but a real obligation in some
   jurisdictions.
4. **The masking filter is pattern-based** and will not catch a novel identifier format. Adding a
   document type with a new identifier means adding its pattern.
