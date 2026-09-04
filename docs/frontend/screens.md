# Frontend — Screens

## Structure

```
frontend/src/app/
├── (auth)/login/                  ← no Stitch design
├── (dashboard)/
│   ├── layout.tsx                 sidebar + top bar
│   ├── dashboard/                 ← Stitch: admin_dashboard
│   ├── documents/
│   │   ├── page.tsx               ← no Stitch design
│   │   └── [id]/                  ← no Stitch design
│   ├── upload/                    ← Stitch: upload_documents
│   ├── chat/
│   │   ├── page.tsx               ← Stitch: ai_rag_chat
│   │   └── [id]/
│   ├── settings/                  ← no Stitch design
│   └── super-admin/               Release 2 — Super Admin only
│       ├── organizations/         built
│       ├── categories/            built
│       └── agent-rules/           DESIGNED, NOT BUILT — §9
└── api/                           BFF route handlers — token stays server-side
```

**Three of the nine screens below have a Stitch design.** The rest are derived from
[design-system.md](design-system.md). That derivation is explicit, not silent: the design system
is complete enough to build from, but they were never visually approved. The Release 2 Super
Admin screens are in that group — Organizations and Categories are built without a design, and
Agent Rules (§9) is designed on paper only.

## 1. Login — no design

Centred card on `background`. Wordmark, email, password, submit, inline error.

States: idle · submitting (button disabled, spinner) · error (inline, password cleared, email
kept) · redirecting.

Uses a Next.js server action. The refresh token becomes an `httpOnly` cookie; **no token reaches
client JavaScript**.

## 2. Dashboard — Stitch: `admin_dashboard`

Four stat cards (Total, Completed, Processing, Failed), a Recent Documents table, Quick Actions,
and a live Processing Queue.

**Rebuilt responsive.** The Stitch version is desktop-only: its `<aside>` lacks the
`hidden md:flex` that the other two screens have, and its `<nav>` is
`fixed ml-[280px] max-w-[calc(100%-280px)]`. Below ~768px it breaks.

The Processing Queue polls `GET /documents/{id}/status` for in-flight documents. It shows the
**stage label** as the primary signal — "Extracting text…", "Generating embeddings…" — with the
bar secondary, because `progress_percent` is derived from stage position, not measured, and OCR
on a long scan sits at one value for minutes.

States: loading (skeleton cards) · empty (no documents yet) · error · populated.

## 3. Upload — Stitch: `upload_documents`

Dropzone, selected-file list with per-file status, actions.

Client validation runs on selection using limits from `GET /config/upload-limits`, so it cannot
drift from the server. Per-file states:

```
validating → ready | invalid → uploading → queued | duplicate | rejected → processing → completed | failed
```

The Stitch markup already shows the valid and "File too large" variants. Missing and required:
duplicate, uploading, and post-upload processing.

After upload the list becomes a **results list** and accepted rows begin polling. This is the
part with no design at all — the Stitch screen ends at the button.

One invalid file never blocks the others (§19).

## 4. Documents — no design

The list is the most-used screen in the product and the least designed. Built from the dashboard
table's visual language.

Table: name (with type icon) · category · status chip · size · pages · uploaded · actions.
Above it: search, filter bar (status, category, type, language, date range), sort on headers,
pagination below.

Filters live in the URL, so a filtered view is shareable and survives refresh.

States: loading · empty (no documents) · **no results** (filters match nothing — distinct from
empty, and needs a "clear filters" action) · error · populated.

`FAILED` rows show their `error_code` and a Reprocess button, so diagnosis and fix are adjacent.

**As built (Phase 5).** No per-row actions column: a row opens the drawer, and every action lives
there. One action surface rather than two means Delete cannot be reached without first seeing
what is being deleted.

## 5. Document detail — no design

A drawer over the list, not a route change, so list state survives.

Metadata, tags, processing timeline, chunk count, and actions (Edit · Download · Reprocess ·
Delete).

For identity documents, extracted fields render **masked by default** with an explicit reveal
that writes an audit row — [../security/pii-handling.md](../security/pii-handling.md).

Delete confirmation names the file.

**As built (Phase 5).** Everything above exists except the masked identity fields: the pipeline
extracts and validates PAN and Aadhaar values but does not persist them, so there is nothing to
mask yet. See [../implementation-status.md](../implementation-status.md).

## 6. Chat — Stitch: `ai_rag_chat`

The strongest artifact. Sidebar with date-grouped history and search; 800px message column;
source chips already carrying `Page 4` and `Sec 2`; auto-growing textarea.

Converted as-is, with three changes:

- The inline `oninput` textarea handler becomes React state.
- The commented-out empty state is restored as a real state.
- The mobile drawer gets actual behaviour — Stitch has the hamburger and
  `-translate-x-full md:translate-x-0`, but no JavaScript, so it is inert.

States: empty ("How can I help you today?") · thinking · streaming · error with retry ·
**not-found** (grounded refusal, no source chips — visually distinct from an error, because it is
a correct answer).

**As built (Phase 6).** Every state above except **streaming**: answers arrive whole. The refusal
is styled as a muted answer, not as an error — confusing the two teaches the admin to distrust
both. A chip whose document has since been deleted renders greyed and unlinked rather than
pointing at a 404.

**Answer rendering.** The composer returns Markdown and picks the shape per question, so the
bubble renders Markdown rather than preformatted text: sentences, paragraphs, bulleted and
numbered lists, tables, and fenced code blocks. A wide table scrolls inside its own box so it
cannot stretch the 800px column, and headings render as `h3`/`h4` so an answer never out-ranks the
page's own heading.

> 🔴 **This content is untrusted.** It is written by a model that has just read admin-uploaded
> documents, so a document is an injection path into the renderer. `rehype-raw` is deliberately
> **not** used — raw HTML is escaped and displayed as text — and link hrefs are filtered to
> http/https/mailto so `javascript:` cannot survive. Both are asserted in
> `src/components/chat/Markdown.test.tsx`.

The user's own message stays plain text: someone typing `# hello` meant a hash, not a heading.

## 7. Settings — no design

Taxonomy management (the configurable categories of §24), upload limits (read-only, from config),
and account. Low priority; can ship after Phase 6.

## 8. Shared layout

`(dashboard)/layout.tsx` renders one `Sidebar` and one `TopBar` around every page. Stitch repeats
both across three files with divergent nav sets — that divergence is not preserved.

## 9. Agent Rules — Super Admin, no design

> **Designed, not built.** [agent-rules.md](../ai/agent-rules.md) §5 ·
> [ADR-009](../architecture/decisions/ADR-009-dynamic-agent-rules.md).

`/super-admin/agent-rules`. A third entry in the Super Admin nav, after Organizations and
Categories. Not reachable by an Organization Admin: the nav does not render it and the backend
answers 403.

**List.** One row per chat agent from the registry — display name, one-line role, and whether a
custom rule is in effect or the shipped default is running. Two rows today (Query Planner,
Response Composer); Agent 1 is out of scope.

**Editor.** A plain monospace `<textarea>`. No toolbar, no syntax highlighting, no rule builder.
The requirement is plain text and the UI must not imply otherwise — a JSON-looking editor would
invite a Super Admin to write JSON, which is not what is read.

**The read-only half is not optional.** Below the editor, the text the code always appends — the
grounding rules and the JSON output contract — shown but not editable. A Super Admin needs to
see the part they cannot change. Hiding it invites them to paste an output schema of their own
and then wonder why nothing takes effect.

**Reset to default** clears the content, which restores the shipped prompt. It is not a delete,
and it is not styled as one.

**No delete control**, because there is no delete endpoint (§7 of the feature doc). The absence
is the design, not a hidden button.

States, per §43: empty (no custom rule — show the default, clearly labelled as the default),
saving, saved, and error. There is no loading-forever state: a rule that cannot be read is not
an error the page reports, it is the default running.

## BFF pattern

Every backend call goes through a Next.js route handler under `app/api/`:

```
Browser → /api/documents → (attach bearer token server-side) → FastAPI
```

The browser never holds a backend token and never sees `GEMINI_API_KEY`, `DATABASE_URL` or any
other secret (§40, §42). **No `NEXT_PUBLIC_*` variable may hold a secret** — Next.js inlines them
into the client bundle at build time, which publishes rather than configures them.

## Required states (§43)

Every screen handles loading · success · error · empty · partial success · retry · disabled ·
processing. Not just the happy path.

Two distinctions that are easy to collapse and shouldn't be:

- **Empty vs no-results.** "No documents yet" invites an upload; "no documents match these
  filters" invites clearing them.
- **Error vs not-found in chat.** A grounded refusal is a correct answer and must not look like a
  failure.

## Polling

```
poll GET /documents/{id}/status every 2s
  → back off to 5s after 30s
  → stop at COMPLETED | FAILED | DUPLICATE | DELETED
```

The API returns `is_terminal`, so the client never hard-codes the terminal set. One shared
`useDocumentStatus` hook serves the dashboard queue, the upload results list and the document
detail drawer.
