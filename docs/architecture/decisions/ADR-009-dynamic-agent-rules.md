# ADR-009 — File-Based Dynamic Agent Rules

**Status:** Accepted · **Date:** 2026-08-31 · **Decides:** Dynamic Agent Rules requirement
· **Amends:** [ADR-002](ADR-002-crewai-three-agents.md)

## Context

Agent personas are constants in code today. `query_planner.py` and `response_composer.py` each
declare `ROLE`, `GOAL` and `BACKSTORY` at module level, and `response_composer` additionally
holds two blocks of format guidance. Changing how an agent speaks therefore requires a code
change, a review and a deploy.

The requirement is that a **Super Admin** may edit those instructions from a page in the
application, that the text is **plain text**, and that it is stored in a **file rather than the
database**, at a path the application controls.

ADR-002 fixes the agent roster at three and requires a new ADR to change it. This ADR adds no
agent. It changes how two of them are *configured*, which is close enough to that boundary to be
worth recording rather than assuming.

## Decision

Agents 2 and 3 load part of their prompt from a fixed file at call time. Everything else stays
in code.

### The editable envelope

A prompt sent to agent 3 is not one thing. It is three, and they carry different consequences:

| Part | Example | Editable |
| ---- | ------- | -------- |
| **Guidance block** | "a direct question gets ONE sentence"; "rewrite the question as a standalone search query" | ✅ |
| Persona | `ROLE`, `GOAL`, `BACKSTORY` | ❌ |
| **Grounding rules** | "Use ONLY the passages above", "Do not invent facts", "Cite only ids from the list above" | ❌ |
| **Output contract** | `Return JSON only. {"answer": …, "is_grounded": …, "cited_chunk_ids": […]}` | ❌ |

**One slot, not several.** A rule file replaces exactly one block in the assembled task prompt
and nothing else. Everything around it — the question, the passages, the taxonomy, the grounding
rules and the JSON contract — is assembled by code on every call.

> **Amended 2026-08-31, during Phase 3–4.** This table first listed `ROLE`/`GOAL`/`BACKSTORY` as
> editable alongside the guidance. Building it showed that to be two knobs wearing one name: the
> requirement is a single plain-text rule per agent, and splitting one textarea across a CrewAI
> identity triple and a task block would need a file format — YAML or headed sections — which the
> requirement explicitly rules out. The persona therefore stays in code and the guidance block is
> the editable slot. Recorded rather than quietly changed, because the earlier table is what the
> feature doc and the security model were written against.

### Why the split is the decision

Without it, two edits that look harmless disable the system silently:

**Deleting the output contract.** Agent 3 stops returning JSON. `clean_json()` returns `None`,
`compose()` falls back to the templated answer, and every chat degrades to a passage dump. HTTP
200, no error code, no failed request, nothing in the error log. The fallback that exists to
survive an outage would instead hide a typo — indefinitely.

**Softening the grounding rules.** The model answers from its own knowledge with
`is_grounded: true`. The citation check intersects `cited_chunk_ids` with the ids actually
supplied, so *invented ids* are dropped — but an invented *claim* attached to a real id passes
every check the system has. The one guarantee this product makes is that answers come from the
documents. It cannot be editable through a textarea.

The split is what makes the feature safe to expose, so it is a decision and not an
implementation detail.

## Storage

One file per agent, under `backend/config/agent_rules/`.

```
backend/config/agent_rules/
  query_planner.txt
  response_composer.txt
```

`config/` is the application-controlled configuration package — it already holds `settings.py`
and `taxonomy.py`. Deliberately **not**:

- `storage/` — that is tenant document data, per-organization and user-supplied.
- `logs/` — write-only, rotated, and reasonably deleted at any time.

Agent rules are global. They are not per-organization, and an Organization Admin cannot see or
change them: a tenant editing the grounding rules would be editing them for every other tenant.

### Fixed paths, not validated paths

The API accepts an **agent key**, never a path. The key is looked up in a hard-coded registry;
an unrecognised key is a 404.

`LocalStorageService._path()` sets the alternative precedent — resolve the path, then verify
containment, with the standing note that *"checking for `..` in the string alone is not
sufficient."* That is the right defence when a key genuinely comes from a client. Here nothing
does. A registry makes path traversal **unrepresentable** rather than defended against, and an
attack that cannot be expressed needs no test to prove it is blocked.

## Fallback

Unchanged, and load-bearing.

```
rule file readable and non-empty  →  use it
anything else                     →  use the in-code constants
```

"Anything else" is: no file, empty file, whitespace only, unreadable, permission denied,
oversized, or disappears between two requests. None of these fail a chat. Each is a WARNING in
the log, because a rule that silently stopped applying looks exactly like one that was never
edited.

The agents' existing non-agent fallbacks — retrieve on the raw question, return the templated
answer — sit *below* this and are untouched. A missing rule file degrades to a working prompt;
it does not reach the agent's own fallback at all.

## Consequences

- Prompt changes stop being code changes, which is the point, and also the risk: they skip
  review. The audit action `agent_rule.updated` is the compensating control. This mirrors
  `organization_admin.contact_changed` in Release 2 — a Super Admin power that authorization
  cannot restrict, so the guarantee becomes *cannot be exercised silently*.
- A read is added to the synchronous chat path. It is a small local file; if measurement ever
  says otherwise, cache on mtime rather than on a TTL, so a save takes effect on the next
  request instead of eventually.
- **Both the API and the Celery worker import these modules.** Chat is API-only, so today one
  process writes and reads. Extending to agent 1 (worker-only) makes the worker a reader that
  never sees a save; that is the first thing to solve if the scope grows, and the reason
  caching must key on mtime rather than process lifetime.
- Writes must be atomic — temp file, then replace. A crash mid-write would otherwise leave a
  truncated prompt, and a truncated prompt is a *valid* prompt: it would be used, not rejected.
- Editable text reaches the model, so it is prompt-injection surface. The author is a Super
  Admin, the most trusted role in the system, so this is a trust question rather than a
  validation one. It is bounded by the envelope above: whatever the persona says, the grounding
  rules and the output contract are appended after it.

## Scope

Agents 2 and 3 only.

Agent 1 (Identity Document) is deliberately excluded. It runs in the Celery worker, and its
output feeds PAN and Aadhaar validation — a persona edit there changes what is recognised as a
government identifier. Its envelope would need to be narrower than agent 3's, not the same one,
so it is a separate decision and a separate ADR if it is ever wanted.

## Alternatives rejected

**Database storage.** The natural choice — transactional, versioned for free, no filesystem
concerns. Excluded by requirement. Recorded so the trade-off is not re-litigated: what is given
up is edit history and atomic multi-agent updates, and what is gained is that a prompt is
diffable, greppable and reviewable as a file.

**Whole-prompt editing.** Simpler to build and simpler to explain. Rejected for the two silent
failures above. If it is ever adopted, it needs its own ADR recording those as accepted risks.

**Per-organization rules.** Would let a tenant tune its own chatbot. Rejected for now: the
grounding rules are the product's central guarantee, and multiplying the number of people who
can weaken them multiplies the ways it can be weakened. Revisit only with the envelope enforced.
