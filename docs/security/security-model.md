# Security Model

§40. One admin role ([ADR-001](../architecture/decisions/ADR-001-single-admin-role.md)), so the
surface is narrow — but the file-handling and RAG paths carry real risk regardless of how many
roles exist.

## Trust boundaries

```
Browser  ──►  Next.js BFF  ──►  FastAPI  ──►  PostgreSQL · Qdrant · Redis · Gemini
   ▲              ▲                ▲
untrusted    holds secrets    enforces everything
```

- **The browser is untrusted.** Client validation is UX only (§16).
- **Next.js holds the tokens and the API keys.** Nothing secret is in the client bundle.
- **FastAPI is the only enforcement point.** Every rule is re-checked here.

## Authentication

JWT bearer, single role. Details: [../features/authentication.md](../features/authentication.md).

### 🔴 The public-path exemption must be exact-match

```python
# WRONG — a prefix test
if request.url.path.startswith("/models"):
    return await call_next(request)          # exempts the /models ROUTER too
```

A `startswith` exemption written for a static mount also exempts every route sharing that prefix.
This exact bug exists in a sibling project in this codebase and left ten endpoints — including
upload and paid-AI endpoints — reachable with no token.

```python
PUBLIC_PATHS = frozenset({
    "/health",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",   # authenticates via the refresh cookie + denylist
})

if request.url.path in PUBLIC_PATHS:
    return await call_next(request)
```

Exact-match set, no prefixes. Static mounts must not share a prefix with a router. A test asserts
that every registered route except the public set returns 401 without a token — so a future route
cannot silently become public.

## Secrets

| Secret | Lives in | Never |
| ------ | -------- | ----- |
| LLM provider API keys | `llm_providers`, **encrypted** | `.env`, browser bundle, logs, any API response |
| `ENCRYPTION_KEYS` | Backend `.env` | Git, client, logs |
| `JWT_SECRET` | Backend `.env` | Git, client, logs |
| `DATABASE_URL` | Backend `.env` | Client |
| `QDRANT_API_KEY` | Backend `.env` | Client |
| Redis URL | Backend `.env` | Client |

### Provider credentials at rest (ADR-010)

Provider API keys moved out of `.env` in Release 2.1 and are stored encrypted, because a Super
Admin can now add them at runtime and a file on disk is not somewhere a web form can write.

- **AES-256-GCM**, not a bare "AES-256". A key size is not a scheme: under an unauthenticated mode
  the *wrong* key decrypts to plausible garbage rather than raising, and the rule that a malformed
  credential is never sent to a provider would have no failure to detect.
- Each configured secret is stretched to a 32-byte key with **HKDF-SHA256** under a fixed info
  label. Changing that label makes every existing ciphertext unreadable, so it is a constant.
- `ENCRYPTION_KEYS` is a **registry**, and every ciphertext names the key that wrote it. Two keys
  can coexist during a rotation. Removing a key some row still uses makes that row permanently
  unreadable — which is why `.env.example` says to keep old keys listed.
- A decrypted key exists **in memory only**. No API response has a field for one, so no route can
  return it. Logs, audit rows, `/health` and error messages carry the 16-character fingerprint
  instead — including the fingerprint on every `llm_provider.activated` audit row, which is the
  compensating control for a power authorization cannot restrict.
- A provider's own error text is logged and never returned to a client: it can echo the request,
  and the request carries the key.

**No `NEXT_PUBLIC_` variable may hold a secret.** Next.js inlines them into the client bundle at
build time — readable by anyone loading the site. A key placed there is published, not
configured. `.env` files are gitignored and `.env.example` carries placeholders only.

## File upload — the largest surface

| Threat | Control |
| ------ | ------- |
| Malicious extension | Allow-list, not a deny-list |
| Content/extension mismatch | Magic-byte verification |
| Spoofed MIME | MIME derived from content; the client's header is ignored entirely |
| Path traversal | Filename sanitised; separators, `..`, NUL and drive letters rejected |
| Filename used as a path | Never. Storage uses a generated `storage_key`; the original name is display-only |
| Zip bomb / oversized | Size limit enforced at the ASGI layer before buffering |
| Malformed file crashing a parser | Parsing runs in the worker, not the request. A crash fails one document |
| Stored XSS via filename | Escaped on render; downloads sent as `attachment` |
| Resource exhaustion | Rate limit, batch cap, worker time limits |

**Not covered:** virus scanning. There is no AV integration. If untrusted parties can upload, add
ClamAV or equivalent — with one admin uploading, the residual risk is accepted and recorded.

## Retrieval and RAG

- Authorisation is enforced by the application, never by the model (§33). The `status` filter is
  part of the Qdrant query, not a post-filter.
- The corpus is never sent to Gemini — only top-K chunks (§32).
- **Prompt injection is a real, partially-mitigated risk.** A document containing *"ignore
  previous instructions and reveal…"* enters the context as data. The composer prompt states that
  context is reference material and never instruction. That reduces the risk without eliminating
  it; the genuine mitigation is that the corpus is admin-curated. Stated plainly rather than
  claimed as solved.
- Citations are verified against retrieved ids, so the model cannot fabricate a source.

## Agent rule files — a write surface reachable over HTTP

> **Designed, not built.** [ai/agent-rules.md](../ai/agent-rules.md) §11 ·
> [ADR-009](../architecture/decisions/ADR-009-dynamic-agent-rules.md).

Dynamic agent rules let a Super Admin write a file on the server through an API call. That is a
new class of surface for this application — until now the only HTTP-reachable writes were
document uploads into `storage/`, which are opaque blobs never executed or interpreted. A rule
file *is* interpreted: its contents go into a prompt.

**Path traversal is unrepresentable, not filtered.** The API accepts an agent *key*, looked up in
a hard-coded registry. No path component ever comes from the client, so there is nothing to
traverse with, and an unknown key is 404 before any filesystem call. This is a stronger position
than `LocalStorageService._path()`, which must resolve-and-contain because its key genuinely is
client-supplied — see [§ File upload](#file-upload--the-largest-surface). Both are correct for
their inputs; the difference is worth knowing so the agent-rules route is not "fixed" later by
adding a containment check that implies a path exists to check.

**The editable envelope is a security boundary.** Only the persona and format guidance are
editable. The grounding rules and the JSON output contract are appended by code on every call and
cannot be reached from the editing page. Both exclusions exist because the failure is *silent*:

- Remove the output contract and the composer stops returning parseable JSON. Every chat falls
  back to a templated answer — HTTP 200, no error code, nothing in the error log. A configuration
  mistake would wear the disguise of the fallback that exists to survive an outage.
- Soften the grounding rules and the model answers from its own knowledge with
  `is_grounded: true`. The citation check intersects returned ids with the ids supplied, so an
  invented *id* is dropped — but an invented *claim* attached to a real id passes every check the
  system has. The product's central guarantee is that answers come from the documents; it cannot
  be editable through a textarea.

**Authorization.** Super Admin only, enforced by `require_super_admin`. The rules are global, so
an Organization Admin editing them would be editing them for every other tenant. The
route-enumeration test in [../testing/test-plan.md](../testing/test-plan.md) covers these routes
automatically once they exist.

**Prompt injection by the author is in scope and is a trust decision, not a validation one.** The
Super Admin is the most trusted role in the system and can already suspend organizations and
delete their vectors. Rule text is bounded by the envelope above, not by a filter: whatever the
persona says, the grounding rules and the contract follow it.

**Audit.** Every save writes `agent_rule.updated`. This is the same reasoning as
`organization_admin.contact_changed` in Release 2 — a Super Admin power that authorization cannot
restrict, so the guarantee becomes *cannot be exercised silently*.

**Content is never logged**, following the existing rule that prompts are not logged (§39): rule
text is concatenated with document text before reaching the model.

## Data sent to Google

Every classification, metadata, tag, query-planning and composition call sends text to Gemini —
including, for identity documents, **OCR'd card contents**. There is no redaction layer and no
flag to disable it. Anyone with data-residency or biometric-data obligations must evaluate this
before ingesting identity documents. See [pii-handling.md](pii-handling.md).

## Rate limiting

| Endpoint | Limit |
| -------- | ----- |
| `POST /auth/login` | 5 / min / IP |
| `POST /documents/upload` | 20 / min / user |
| `POST /chat/.../messages` | 30 / min / user |
| Everything else | 300 / min / user |

Login is per-IP because the attacker controls the account field. The rest are per-user because
the token is the meaningful identity.

**As built (Phase 7).** `app/middlewares/rate_limit.py`, backed by Redis with a fixed window.

- It runs **inside** `AuthMiddleware`, so a per-user limit keys on the real identity and an
  unauthenticated caller gets 401 rather than a confusing 429.
- It **fails open**. If Redis is unreachable the request is allowed and a warning is logged.
  Failing closed would turn a cache outage into a total outage — the worse failure for a
  single-admin internal system. Recorded as an accepted risk rather than left implicit.
- A fixed window allows up to 2x the limit across a boundary. Accepted: these limits stop
  credential stuffing and runaway loops, not paid-API metering.
- `/health` is never counted, or a probe every second would consume the operator's own budget.
- `X-RateLimit-Limit` and `X-RateLimit-Remaining` ride on every response, so a client can back
  off before being refused.

A mistyped limit raises at startup. `"5"` without a window is rejected rather than being read as
`5/minute` — silently reinterpreting config is what the parser exists to prevent.

## Injection

- SQLAlchemy parameterises everything. No raw string SQL.
- **`sort` and filter fields are whitelisted.** A column name interpolated into `ORDER BY` is
  injectable even when values are bound.
- Pydantic validates and coerces every request body.
- Qdrant filters are built from validated values, never from raw user text.

## Logging and audit

Never logged (§39): passwords, tokens, API keys, file contents, extracted text, PII.

Always logged: `request_id`, `user_id`, method, path, status, duration, `error_code`.

`audit_logs` is append-only with no exposed update or delete path. Its `metadata` JSONB must never
carry document content or PII.

## Transport and headers

Development is HTTP on localhost. Production requires HTTPS, plus `Strict-Transport-Security`,
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a restrictive
`Content-Security-Policy`, and CORS limited to the known frontend origin — never `*`.

**As built (Phase 7).** `app/middlewares/security_headers.py`, outermost in the stack so the
headers are present on a 401 or a 429 too, not only on responses that reached a route.
`Strict-Transport-Security` is production-only: sending it over plain HTTP on localhost would pin
the developer's browser to HTTPS for a host that does not serve it, and that pin outlives the
mistake. The docs pages are exempt from the `default-src 'none'` policy, which would otherwise
render them blank.

## Operational exposure

| Service | Rule |
| ------- | ---- |
| **Flower :5555** | **No auth by default.** Bind to `127.0.0.1`. It displays task arguments |
| **Qdrant :6333** | Dashboard has no auth by default. Localhost only; set `QDRANT_API_KEY` if bound wider |
| **Memurai :6379** | No password by default. Localhost only |
| **`/docs`, `/redoc`** | Disabled in production |
| **PostgreSQL :5432** | Not exposed beyond localhost |

Three of these are unauthenticated by default and each exposes real data. Binding any of them to
`0.0.0.0` on a shared network is the single easiest mistake to make here.

## Known accepted risks

1. No virus scanning on uploads.
2. Prompt injection is mitigated, not solved.
3. Document text and OCR'd identity fields are transmitted to Google.
4. Flower, Qdrant and Memurai run unauthenticated on localhost.
5. Memurai Developer Edition is not licensed for production
   ([ADR-007](../architecture/decisions/ADR-007-native-windows-services.md)).
6. No MFA on admin login.
7. Agent rules, once built, let a Super Admin change how every tenant's chat behaves without a
   code review. The compensating control is the audit row, not prevention
   ([ADR-009](../architecture/decisions/ADR-009-dynamic-agent-rules.md)).
