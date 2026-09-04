# Frontend — Design System

Derived from `stitch_documind_ai_interface/cognitive_enterprise/DESIGN.md` and the three Stitch
`code.html` files, with the three conflicts resolved in
[ADR-008](../architecture/decisions/ADR-008-design-system-resolution.md).

## Product name

**EnterpriseRAG**, everywhere — `<title>`, wordmark, sidebar brand, `package.json`, FastAPI
`title=`. The Stitch strings `DocIntelligence` and `Enterprise AI` are replaced during
conversion. The sidebar tagline *"RAG Knowledge Base"* is kept.

## Colour

56 Material-3 tokens, taken from the Stitch config verbatim. Key values:

| Token | Value | Use |
| ----- | ----- | --- |
| `primary` | **`#15157d`** | Primary actions, wordmark, active nav, headings |
| `primary-container` | `#2e3192` | Hover for primary |
| `on-primary` | `#ffffff` | |
| `secondary-container` | `#d0e1fb` | Active nav background |
| `surface` / `background` | `#f7f9fb` | Page ground |
| `surface-container-lowest` | `#ffffff` | Cards, sidebar |
| `on-surface` | `#191c1e` | Body text |
| `on-surface-variant` | `#464652` | Secondary text |
| `outline-variant` | `#c7c5d4` | Borders |
| `error` / `error-container` | `#ba1a1a` / `#ffdad6` | |
| `on-tertiary-container` | `#32b4f9` | AI accent |

`primary = #15157d` is settled — the prose in `DESIGN.md` calling `#2E3192` the anchor is
corrected, since the entire tonal family is derived around `#15157d`.

### Status tokens — new

The Stitch dashboard hard-codes `#dcfce7` / `#166534` for success; no success token exists. One
token pair per §21 state, so a status chip never carries a literal hex:

| State | Background | Foreground |
| ----- | ---------- | ---------- |
| `COMPLETED` | `#dcfce7` | `#166534` |
| `PROCESSING` + active stages | `secondary-fixed` | `on-secondary-fixed-variant` |
| `QUEUED` | `surface-container-high` | `on-surface-variant` |
| `FAILED` | `error-container` | `on-error-container` |
| `DUPLICATE` | `#fef3c7` | `#92400e` |
| `DELETED` | `surface-container` | `outline` |

## Shape — fully rounded

**`rounded-full` on everything**: buttons, inputs, badges, cards, tables, the dropzone, chat
bubbles.

CSS clamps overlapping radii — a radius exceeding half the box is scaled down proportionally
(CSS Backgrounds §5.5) — so a 300×160 card renders as a **stadium**, not a broken shape.
Well-defined and consistent across browsers.

Consequences to handle in components, not to be surprised by:

- Horizontal padding must increase on tall containers so content clears the curve. Use `px-8`
  where the Stitch comps used `px-4`.
- A table inside a fully-rounded shell needs `overflow-hidden` on the shell; first and last rows
  lose usable corner width.
- This departs from the approved Stitch screenshots on every container.

`DESIGN.md`'s rule that pills are "used exclusively for status badges and the New Chat button" is
superseded.

**Revisit path** if it reads poorly once rendered: the `DESIGN.md` 6-step scale
(`sm 4 · DEFAULT 8 · md 12 · lg 16 · xl 24 · full`) with pills kept for badges and CTAs. That is
a `tailwind.config.ts` change plus class renames — no logic touched.

## Typography

| Role | Family | Size / line | Weight |
| ---- | ------ | ----------- | ------ |
| `display-lg` | Inter | 48 / 56, `-0.02em` | 700 |
| `headline-lg` | Inter | 32 / 40, `-0.02em` | 600 |
| `headline-md` | Inter | 24 / 32 | 600 |
| `body-lg` / `body-md` / `body-sm` | Inter | 18/28 · 16/24 · 14/20 | 400 |
| `label-md` / `label-sm` | **JetBrains Mono** | 14/20 · 12/16 | 500 |

JetBrains Mono is reserved for metadata, document ids, timestamps, citations and version
numbers — the "data-driven" register that separates facts from prose.

Both families load through `next/font` (self-hosted, no layout shift), **not** the Google Fonts
CDN link the Stitch files use.

## Spacing

4px baseline. `xs 4 · sm 8 · md 16 · lg 24 · xl 32`; gutter 24; margins 16 mobile / 48 desktop.
`lg` between major components, `md` inside cards and bubbles.

## Layout

- Sidebar **280px fixed**, collapsing to a drawer below `md`.
- Content is a 12-column fluid grid, max 1400px.
- Chat column max **800px**, centred, for line-length readability.
- Tables become card stacks on mobile.

## Elevation

| Level | Use | Style |
| ----- | --- | ----- |
| 0 | Page | `background` |
| 1 | Cards, sidebar | White + 1px `outline-variant`, no shadow |
| 2 | Popovers, dropdowns | White + `0 10px 15px -3px rgba(0,0,0,0.05)` |
| 3 | Modals | White + deep shadow + 20% backdrop, 8px blur |

Shadows are never pure black — tinted toward neutral-900.

## Components

Built once, in `src/components/ui/`, and reused. The Stitch files repeat markup across screens
with small divergences; that is the thing conversion must not preserve.

| Component | Notes |
| --------- | ----- |
| `Button` | `primary` · `secondary` · `ghost` · `danger`, three sizes |
| `Input` / `Textarea` | Focus: `primary` border + 3px soft indigo ring |
| `Badge` | Status chips, driven by the status tokens above |
| `Card` | Fully rounded, 1px border, generous horizontal padding |
| `Table` | Sortable headers, `overflow-hidden` shell, mobile card-stack |
| `Sidebar` | **One component**, with an optional chat-history slot |
| `TopBar` | Search, notifications, avatar |
| `FileDropzone` | Drag state, per-file list, per-file status |
| `ChatBubble` | `user` right / `assistant` left with AI accent border |
| `SourceChip` | `[icon] Name [Page 4]` — mono for the locator |
| `ProgressBar` | Stage label primary, bar secondary |
| `EmptyState` / `ErrorState` / `Skeleton` | Required by §43 on every screen |

### Sidebar unification

Stitch ships three sidebars with three different nav sets. One component, one nav definition:

```
Dashboard · Documents · Upload · Chat · Settings        ── main
[chat history, only on /chat]                           ── slot
Help · Sign Out                                         ── footer
```

## What is dropped from the Stitch HTML

| Dropped | Replaced by |
| ------- | ----------- |
| `cdn.tailwindcss.com` | Local Tailwind build |
| Google Fonts `<link>` | `next/font` |
| Material Symbols CDN | `lucide-react`, tree-shaken |
| `lh3.googleusercontent.com` avatars | Local placeholder |
| `docked full-width` classes | Nothing — not Tailwind, no-ops |
| `rounded-2xl` (undefined in both configs) | Moot under the fully-rounded decision |
| Inline `oninput` handler on the chat textarea | React state |

## Accessibility

Not present in the Stitch output and required (§13):

- Every icon-only button gets an `aria-label`.
- Focus rings are visible — never `outline: none` without a replacement.
- The mobile drawer traps focus and closes on Escape.
- Status is conveyed by text as well as colour, so a chip is not colour-only.
- Contrast: `#15157d` on white ≈ 14:1; `on-surface-variant` on `surface` passes AA.
- `prefers-reduced-motion` disables the dashboard's pulse animation.
