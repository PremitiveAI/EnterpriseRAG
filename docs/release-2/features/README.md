# Release 2 — Feature Documentation

Seven features, each following the same 15-section structure as the Release 1 feature docs:
requirement, business rules, user flow, backend flow, frontend flow, database impact, API
contract, Celery impact, Qdrant impact, Redis impact, security, error handling, edge cases,
testing, acceptance criteria.

Design rationale and the cross-feature picture live in [../README.md](../README.md).

---

## Read in this order

| # | Feature | What it covers | Depends on |
| - | ------- | -------------- | ---------- |
| 1 | **[Tenant isolation](tenant-isolation.md)** | The four enforcement layers. **Read this first** — every other feature assumes it | — |
| 2 | [OTP authentication](otp-authentication.md) | Organization Admin login, no password | — |
| 3 | [Organization management](organization-management.md) | Super Admin CRUD, collection provisioning | 1 |
| 4 | [Organization admin management](organization-admin-management.md) | Creating admins, the contact-change audit | 2, 3 |
| 5 | [Organization-scoped documents](organization-scoped-documents.md) | How Release 1 upload and management change | 1, 3 |
| 6 | [Organization-scoped chat](organization-scoped-chat.md) | How Release 1 RAG chat changes | 1, 5 |
| 7 | [Public chatbot](public-chatbot.md) | The unauthenticated widget and hosted page | 5, 6 |
| 8 | [Organization settings](organization-settings.md) | Per-tenant caps and rate limits | 3 |
| 9 | [Category management](category-management.md) | The global taxonomy master | — |
| 10 | [LLM provider management](llm-provider-management.md) | **Release 2.1.** Which model answers, switchable without a restart | — |

---

## New versus modified

| Feature | Type | Release 1 document it changes |
| ------- | ---- | ----------------------------- |
| Tenant isolation | **New** — cross-cutting | Affects all of them |
| OTP authentication | **New** | Extends [authentication.md](../../features/authentication.md) |
| Organization management | **New** | — |
| Organization admin management | **New** | — |
| Organization-scoped documents | **Modifies** | [document-upload.md](../../features/document-upload.md), [document-processing.md](../../features/document-processing.md), [document-management.md](../../features/document-management.md), [duplicate-detection.md](../../features/duplicate-detection.md) |
| Organization-scoped chat | **Modifies** | [rag-chat.md](../../features/rag-chat.md), [conversation-management.md](../../features/conversation-management.md) |
| Public chatbot | **New** | — |
| Organization settings | **New** | — |
| Category management | **New** interface over an existing table | — |
| LLM provider management | **Modifies** | [agents.md](../../ai/agents.md) — all three agents now resolve their provider from the database |

Release 1 documentation is **not** superseded. Where a Release 2 document says "modifies", the
Release 1 rules still apply and only the scope changes.

---

## The three decisions everything else follows from

**1. The organization comes from the token.** No authenticated route reads an organization id from
a path, query or body. The single exception is the public chat endpoint, which pays for that
untrusted input by restricting itself to published documents.

**2. One Qdrant collection per organization.** A forgotten payload filter returns every tenant's
chunks; a wrongly-chosen collection returns nothing. When the requirement is stated as mandatory,
the failure mode is the whole argument.

**3. Tenant parameters have no defaults.** `organization_id` is required and non-defaulted on
every repository method that touches tenant-owned data. An optional filter fails open, and looks
identical to a correct one in review.

---

## What these documents deliberately do not claim

Release 2 OTP login is **not an authentication boundary**: the code is `1111` on localhost and
there is no dispatch until Release 3. Anyone who knows an admin's email can log in. That is
acceptable only because the deployment is localhost-only, and it is stated in
[otp-authentication.md §11](otp-authentication.md#11-security-considerations) and
[../README.md §13](../README.md#13-what-release-2-is-not) rather than glossed over.

The Super Admin privacy guarantee is **"cannot read silently"**, not "cannot read". A Super Admin
who repoints an admin's email can receive their OTP; what prevents it being quiet is a dedicated
audit action. See
[organization-admin-management.md §4](organization-admin-management.md#4-backend-flow).
