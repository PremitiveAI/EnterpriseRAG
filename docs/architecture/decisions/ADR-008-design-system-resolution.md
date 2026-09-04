# ADR-008 — Design System Conflict Resolution

**Status:** Accepted · **Date:** 2026-08-21 · **Decides:** spec §13

Phase 0 found three conflicts between `DESIGN.md` and the three Stitch `code.html` files. All
three are resolved here.

---

## 1. Shape language — fully rounded

`DESIGN.md` declares `xl: 1.5rem` (24px); all three HTML configs declare `xl: 0.75rem` (12px),
so cards rendered at 12px against a specified 16px.

**Decision: `rounded-full` everywhere.** Buttons, inputs, badges, cards, tables, the dropzone
and chat bubbles all take the pill token.

**How this renders.** CSS clamps overlapping radii: when a border-radius exceeds half a box's
dimension, the browser scales all radii down proportionally (CSS Backgrounds & Borders §5.5).
A 300×160 card with `border-radius: 9999px` therefore renders as a **stadium** — fully rounded
left and right ends — not as a broken shape. Behaviour is well-defined and consistent across
browsers.

**Known consequences**, recorded so they are not a surprise when first rendered:

- Wide, short elements (buttons, inputs, badges, nav items) look intentional and consistent.
- Tall containers (stat cards, the dropzone, table shells) bow at the left and right edges, and
  horizontal padding must increase so content does not collide with the curve.
- Data tables inside a fully-rounded shell need `overflow: hidden` on the shell, and the first
  and last rows lose usable corner width.
- This departs from the approved Stitch screenshots on every container.

`DESIGN.md`'s own rule — *"Pill: used exclusively for status badges and the main New Chat
button"* — is **superseded** by this decision.

**Revisit trigger:** if the rendered dashboard reads poorly, the fallback is the DESIGN.md
6-step scale (`sm 4 · DEFAULT 8 · md 12 · lg 16 · xl 24 · full`) with pills kept for badges and
CTAs. That change touches only `tailwind.config.ts` and component classes — no logic.

---

## 2. Brand primary — `#15157d`

`DESIGN.md` prose calls `#2E3192` the anchor; the token set and all three HTML files use
`#15157d` for primary actions and `#2E3192` as the hover/container tone.

**Decision: `primary = #15157d`, `primary-container = #2E3192`.** The tokens win; the prose is
corrected.

Rationale: all six artifacts agree, and the whole Material-3 family — `on-primary`,
`primary-fixed` `#e1e0ff`, `primary-fixed-dim` `#c0c1ff`, `on-primary-fixed` `#04006d`,
`on-primary-fixed-variant` `#373a9b`, `inverse-primary` — is tonally derived around `#15157d`.
Reseeding with `#2E3192` would invalidate all of them.

Both pass WCAG AAA on white (~14:1 and ~11:1), so this is identity, not compliance.

---

## 3. Product name — EnterpriseRAG

Four names were live: `EnterpriseRAG` (repo), `DocIntelligence` (`<title>` + top nav),
`Enterprise AI` (sidebar brand), `documind_ai` (stitch folder).

**Decision: `EnterpriseRAG` everywhere** — page `<title>`, wordmark, sidebar brand, `README.md`,
`package.json` `"name"`, FastAPI `title=` (which drives `/docs`), and all documentation.

The strings `DocIntelligence` and `Enterprise AI` in the Stitch HTML are replaced during
conversion. The sidebar subtitle *"RAG Knowledge Base"* is kept as a tagline.

---

## 4. Additional gaps carried into implementation

Found in Phase 0, not conflicts but must be handled during conversion:

| Gap | Resolution |
| --- | ---------- |
| No success/status tokens — dashboard hard-codes `#dcfce7` / `#166534` | Add semantic tokens for each §21 state |
| Dashboard is desktop-only — its `<aside>` lacks `hidden md:flex` | Rebuild responsive, matching upload/chat |
| Mobile drawer has a button but no JS | Implement as real state |
| Dead classes `docked full-width` on upload's header | Drop — Stitch artifacts, no-ops |
| `rounded-2xl` used by chat bubbles is undefined in both configs | Moot under decision 1 |
| Three sidebars with three nav sets | One `<Sidebar>` with a chat-history slot |
| Tailwind CDN, Google Fonts, Material Symbols, remote avatars | Local Tailwind build, `next/font`, local icon set, local avatar |
