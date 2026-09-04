# ADR-010 — Database-Backed LLM Credentials

**Status:** Accepted · **Date:** 2026-09-02 · **Decides:** Dynamic LLM Provider &
Credential Management · **Amends:** [ADR-002](ADR-002-crewai-three-agents.md)

## Context

The provider is `.env` today. `GEMINI_API_KEY` and `GEMINI_MODEL` are read into `settings` at
import, and every agent reads them from there. Four things follow from that, and all four are
the requirement:

- Changing the model, the key or the provider means editing a file on the server and restarting
  **two** processes — the API and the Celery worker, which is a separate long-lived process.
- The key sits in plaintext on disk.
- Nothing records who changed it, when, or to what.
- There is exactly one provider. Trying a second means overwriting the first.

The requirement is that a **Super Admin** can register several providers, switch the active one
without a restart, and have credentials encrypted at rest.

One thing this does *not* touch: **retrieval**. Embeddings are local sentence-transformers
([ADR-003](ADR-003-local-embeddings.md)), so changing the answering model does not invalidate a
single vector and needs no re-indexing. The blast radius is generation only.

## Decision

Seven parts. Each exists because a simpler version of it fails in a way that is silent.

### 1. The database is the only source of truth

One row in `llm_providers` is active at a time, enforced by the database rather than by
application code:

```sql
CREATE UNIQUE INDEX ix_llm_providers_one_active
  ON llm_providers (is_active) WHERE is_active;
```

Two concurrent activations cannot both win. The loser gets an integrity error, not a system with
two active providers and a coin-flip about which one answers.

Redis is **not** part of this. It is a broker and a cache here and never a source of truth, and
it has no password by default in this deployment — it is the wrong place for a credential.

### 2. The version lives on the row and comes from a global sequence

```sql
CREATE SEQUENCE llm_config_version_seq;

ALTER TABLE llm_providers
  ADD COLUMN config_version BIGINT NOT NULL DEFAULT nextval('llm_config_version_seq');
```

Every insert and every update takes the next value, so a version is greater than **every version
ever issued to any row**, not just to its own.

Per-row counters look equivalent and are not. Two rows both reach version 2 independently:

| | row 1 (gemini) | row 2 (openai) |
| - | - | - |
| version | 2 | 2 |
| active | no | **yes** |

A process warm on OpenAI has cached version 2. The Super Admin activates Gemini. The process
reads version 2, compares `2 == 2`, and **does not rebuild**. It keeps calling OpenAI while the
database, the audit log, the UI and `/health` all say Gemini. Nothing raises, nothing is logged,
and the only symptom is a bill from the wrong vendor.

A global sequence makes that state unrepresentable.

The bump is a **database trigger**, not application code, for the same reason the single-active
rule is an index: one forgotten assignment in one write path reproduces exactly the collision
above, and it fails silently. The trigger fires only when a column that changes the resolved
configuration changes — provider, model, credential, key id, base URL, `config`, `is_active` — so
bookkeeping writes such as `last_tested_at` do not make every process rebuild a client that did
not change.

### 3. Version and config are read in one statement

```sql
SELECT config_version, provider_name, model_name,
       encrypted_api_key, encryption_key_id, base_url, config
FROM llm_providers WHERE is_active LIMIT 1;
```

The version and the configuration it labels come from the same row in the same snapshot, so they
cannot disagree. This is why the version is a **column** and not a separate one-row table: two
reads would be two snapshots, and their correctness would depend on a non-obvious ordering rule.
Read the config first and you can cache *version 6 holding the config as it was at version 5* —
a stale config stamped with the current version, which therefore never rebuilds. The single
statement deletes that race instead of defending against it.

### 4. What is cached is a config, not a client

The factory returns a frozen dataclass:

```python
@dataclass(frozen=True)
class LLMConfig:
    version: int
    provider: str        # gemini | openai | anthropic | azure
    model: str           # bare id, no provider prefix
    api_key: str         # decrypted, in memory only, never logged
    base_url: str | None
    params: dict
```

Each call site keeps its own `lru_cache(maxsize=1)` keyed on that object, so every site rebuilds
its own client when the version changes, with no shared mutable state.

Returning a *client* cannot work, because there is no single type to return. `crewai.LLM` and
`google.generativeai.GenerativeModel` are unrelated types with unrelated constructors; one cache
slot satisfies neither generally. Returning a config sidesteps the question.

The swap is **build-then-swap**: construct the new config completely, then rebind. A reader
never observes a half-built one.

### 5. A database that cannot be read has not changed

The cache records `confirmed_at` from `time.monotonic()` at the last **successful** read — a
monotonic clock, so a wall-clock correction cannot widen or shrink the window.

| Situation | Behaviour |
| --------- | --------- |
| Read succeeds | Stamp `confirmed_at`; rebuild if the version moved |
| Read fails, cache cold | **Fail** — `LLM_CONFIG_UNAVAILABLE`. Nothing to fall back to |
| Read fails, age < 300 s | Serve the cached config, log a WARNING, set `degraded = true` |
| Read fails, age ≥ 300 s | **Fail** — `LLM_CONFIG_UNAVAILABLE` |
| No active row | **Fail** — `LLM_NO_ACTIVE_PROVIDER` |

Failing closed on every database hiccup would be wrong, because the failures are mostly brief and
unrelated to configuration: pool exhaustion (milliseconds), a connection closed while idle (one
call), a restart or failover (5–60 s). In each of these **the configuration has not changed** —
and the cost is worst in the worker, where minutes of extraction and OCR are already spent before
the config is read, and failing there costs a whole pipeline retry.

Never failing would be wrong too: a revoked key would keep working for as long as the process
lived.

The 300-second ceiling is the boundary, and the reason it is defensible is that **while the
database is reachable the two policies are identical** — both read the version and both rebuild.
They differ only when it is unreachable, and the Super Admin's deactivation is a write to that
same database. Exposure needs a specific ordered sequence: the deactivation commits, and *then*
the database becomes unreachable. Within that window the exposure is bounded at five minutes.

**This query must not be routed to a read replica.** Replica lag would reintroduce a stale
window on the *healthy* path, which is the one place this design otherwise has none.

### 6. Credentials at rest: authenticated encryption, key id from day one

```
llm_providers
  encrypted_api_key  TEXT      NOT NULL
  encryption_key_id  SMALLINT  NOT NULL DEFAULT 1
  key_fingerprint    VARCHAR(16) NOT NULL  -- sha256(PLAINTEXT)[:16]
```

**AES-256-GCM** (or Fernet), never a bare "AES-256". A key size is not a scheme, and under an
unauthenticated mode — CBC without a MAC, or ECB — the wrong key decrypts to plausible garbage
instead of raising. The requirement to *not send malformed credentials to a provider* then has
no failure to detect. Authenticated encryption is what turns silent corruption into a catchable
exception, so it is named here rather than left to the implementation.

Keys are a registry, not a single secret:

```
ENCRYPTION_KEYS=1:<secret-one>,2:<secret-two>
ENCRYPTION_ACTIVE_KEY_ID=2
```

This supersedes the single `ENCRYPTION_SECRET` the requirement described. Writes use the active
key; reads use the key the row names.

A configured secret is not used as the AES key directly. Each is stretched to 32 bytes with
**HKDF-SHA256** under a fixed info label, so the encryption key is domain-separated from any other
use of the same string. The derivation is necessarily deterministic — a random salt would make
yesterday's ciphertext unreadable — and **the info label is a constant, not a setting: changing it
makes every existing ciphertext undecryptable.**

A stored value is `v1.<key id>.<base64url(nonce ‖ ciphertext ‖ tag)>`. The key id therefore
appears twice, in the token and in the column, and that is deliberate: the token is authoritative
for decrypting, and the column exists so a rotation job can find the rows that still need work
without decrypting every one of them first. The **re-encryption job is deliberately deferred** — but
the column is not, and the asymmetry is the whole argument:

- Add the column **later**, after a secret has been rotated in place, and the rows are encrypted
  under an unknown mixture of secrets with nothing recording which. The backfill migration you
  would need is exactly the one that cannot be written, and the old secret may already be gone.
- Add the job later and nothing is lost. It can be written at any time, against rows that already
  say what they were encrypted with.

One column now buys the option. Deferring it forecloses it.

`key_fingerprint` is `sha256` of the **plaintext**, truncated. It lets the UI, the audit log and
the 409 check below compare credentials without ever decrypting or displaying one.

### 7. Activation validates first, then commits

```
1.  Test the provider connection            (outside any transaction)
2.  BEGIN
      re-read the row; 409 if key_fingerprint changed
      deactivate the current row
      activate the new row, config_version = nextval(...)
    COMMIT
3.  Audit — provider, model, fingerprint. Never the key.
```

The connection test is a third-party HTTP call, and holding a transaction open across one is the
mistake this ordering avoids. It would hold row locks for the duration, leave a pooled connection
`idle in transaction` for a round trip that is routinely ~15 s and, against a blackholed socket,
runs to the SDK timeout; and `idle_in_transaction_session_timeout` would eventually kill it
mid-call and surface as a confusing commit error rather than as the provider timeout it is.

Validating outside opens a window in which another admin edits the same row. That is closed by
comparing the fingerprint captured at test time against the row re-read inside the transaction,
and answering **409** on a mismatch — cheap, and it needs no lock.

## Bootstrap

An Alembic **data migration** reads `GEMINI_API_KEY` and `GEMINI_MODEL` from the environment,
encrypts under key 1, and inserts one active row.

Not optional, and not a nicety. Without it an upgrade is a **silent outage**: the application
starts, no provider is configured, and every chat quietly falls back to the templated composer —
HTTP 200, a plausible answer, nothing in the error log. `implementation-status.md` already
records exactly this failure once, with agents 2 and 3 dead on every request while `/health`
reported `agents_enabled: true`.

Afterwards `GEMINI_API_KEY` and `GEMINI_MODEL` leave `.env.example` and the settings model, so
the database is the only source. `/health` gains the active provider, its fingerprint and the age
of the last confirmed read, so *"no provider configured"* and *"running provider X"* are both
directly visible rather than inferred.

## Consequences

- **Agent 1 moves onto CrewAI/LiteLLM**, which amends the ADR-002 implementation note. It is the
  only agent still calling `google-generativeai` directly, and leaving it there would mean a
  switch that applies to two agents out of three.
- **Model-id normalisation moves.** `settings._strip_provider_prefix` exists because
  `GEMINI_MODEL=gemini/gemini-2.0-flash` produced `gemini/gemini/gemini-2.0-flash` and **both**
  AI paths failed silently into the templated fallback. That normalisation now belongs on the
  write path — store the bare id, compose the `provider/` prefix in exactly one place. With all
  three agents on LiteLLM the blast radius of getting it wrong is larger than when that comment
  was written, so it needs a test per provider.
- **A database read joins the generation path.** In the API it is negligible: the request already
  requires the database immediately before and after. In the worker it is the one genuinely new
  exposure, and section 5 is what bounds its cost.
- `AnswerResponse.degraded` already exists in the response contract and in the frontend types, so
  the degraded signal needs no new API surface.
- Startup validation splits in two, to stay consistent with `main.py`'s existing rule that
  *"dependency checks are warnings, not failures: the API must still start so /health can report
  what is wrong."* Validating the key **registry** is pure configuration and hard-fails.
  Validating that every row's `encryption_key_id` resolves needs a query, so it is a WARNING plus
  a `/health` field.
- A Super Admin can now change the answering model for every tenant from a web page. As with
  `agent_rule.updated` in [ADR-009](ADR-009-dynamic-agent-rules.md), authorization cannot
  restrict this, so the guarantee is that it **cannot be exercised silently**: every activation
  is audited with the fingerprint.
- Rollback is manual. A Super Admin re-activates the previous row, which takes a new version like
  any other write. There is no automatic revert, because "the provider is misbehaving" is not
  something this system can distinguish from "the documents do not contain the answer".

## Alternatives rejected

**A separate one-row version table.** The obvious way to make the version global. Rejected: it
turns one atomic read into two, and correctness then rests on reading them in the right order —
see section 3.

**A TTL cache.** Simple, and wrong in both directions at once: too long and an activation does
not take effect, too short and every request pays a read to learn nothing changed. A version
check is the same read but answers the question directly.

**Redis pub/sub invalidation.** Would push changes to the worker instantly. Rejected: it makes a
credential path depend on an unauthenticated cache, and it fails silently when Redis is down —
which is precisely when nobody is watching. Polling a version integer is cheaper than being
wrong.

**Keeping `.env` as a fallback when no row is active.** Sounds safe, and hides the exact failure
the seed migration exists to prevent: the system would run on a stale key that nothing in the UI
mentions. Better to fail with `LLM_NO_ACTIVE_PROVIDER` and say so.

**Per-organization providers.** A tenant choosing its own model is a plausible future feature and
is deliberately out of scope. It multiplies the number of places a credential can leak and the
number of people who can point a tenant's documents at a third party. Revisit with its own ADR.
