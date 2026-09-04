# ADR-001 — Single Administrator Role

**Status:** Accepted · **Date:** 2026-08-21 · **Decides:** spec §6.1, §26, §33, §40

## Context

The specification asks for both "secure administrator authentication" (§6.1) and
"permission-aware retrieval" (§26, §33). Those imply different things: the first a single
privileged operator, the second a multi-user system with per-document access rules.

## Decision

**One role: `admin`.** An administrator signs in, uploads documents, manages every document in
the system, and chats against the whole corpus. There are no non-admin users, no groups, no
departments and no per-document ACLs.

## Consequences

- The `users` table carries no `role` column in v1. Every authenticated principal is an admin.
- §33 permission filtering becomes a **soft-delete and status filter**, not an identity filter.
  Retrieval excludes documents whose status is `DELETED`, `FAILED` or `DUPLICATE` — no user
  matching is applied.
- The Qdrant payload carries **no owner or ACL field**, because nothing would read it.
- §32's "Permission Filtering" stage still exists as a named, isolated step in the retrieval
  pipeline. It is currently a status filter. Keeping it as its own function means adding real
  authorization later changes one function, not the pipeline shape.

## Forward compatibility

Adding roles later requires: a `role` column, a `document_access` table, an `owner_id` in the
Qdrant payload, a re-index to backfill that payload, and a filter clause in the retrieval query.
The re-index is the expensive part. That cost is accepted deliberately in exchange for not
building an access-control system nobody has asked to use.

## Alternatives rejected

**Admin + viewer roles now.** Rejected: no requirement describes what a viewer may see, so any
rule would be invented. §4 forbids assuming requirements that materially affect authorization.
