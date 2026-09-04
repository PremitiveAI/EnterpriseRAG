---
name: Cognitive Enterprise
colors:
  surface: '#f7f9fb'
  surface-dim: '#d8dadc'
  surface-bright: '#f7f9fb'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f4f6'
  surface-container: '#eceef0'
  surface-container-high: '#e6e8ea'
  surface-container-highest: '#e0e3e5'
  on-surface: '#191c1e'
  on-surface-variant: '#464652'
  inverse-surface: '#2d3133'
  inverse-on-surface: '#eff1f3'
  outline: '#777683'
  outline-variant: '#c7c5d4'
  surface-tint: '#4f54b4'
  primary: '#15157d'
  on-primary: '#ffffff'
  primary-container: '#2e3192'
  on-primary-container: '#9da1ff'
  inverse-primary: '#c0c1ff'
  secondary: '#505f76'
  on-secondary: '#ffffff'
  secondary-container: '#d0e1fb'
  on-secondary-container: '#54647a'
  tertiary: '#002c42'
  on-tertiary: '#ffffff'
  tertiary-container: '#004362'
  on-tertiary-container: '#32b4f9'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e1e0ff'
  primary-fixed-dim: '#c0c1ff'
  on-primary-fixed: '#04006d'
  on-primary-fixed-variant: '#373a9b'
  secondary-fixed: '#d3e4fe'
  secondary-fixed-dim: '#b7c8e1'
  on-secondary-fixed: '#0b1c30'
  on-secondary-fixed-variant: '#38485d'
  tertiary-fixed: '#c9e6ff'
  tertiary-fixed-dim: '#89ceff'
  on-tertiary-fixed: '#001e2f'
  on-tertiary-fixed-variant: '#004c6e'
  background: '#f7f9fb'
  on-background: '#191c1e'
  surface-variant: '#e0e3e5'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 48px
---

## Brand & Style

The design system is engineered for high-stakes enterprise environments where clarity, speed of information retrieval, and trust are paramount. The brand personality is **authoritative yet accessible**, functioning as an invisible assistant that organizes vast amounts of data without overwhelming the user.

The aesthetic follows a **Modern SaaS** movement, characterized by:
- **Precision:** Perfect alignment and systematic spacing.
- **Clarity:** A "content-first" approach that prioritizes document readability and chat legibility over decorative elements.
- **Sophistication:** Subtle use of depth and color to guide the eye toward primary AI actions.
- **Trust:** A conservative but fresh color palette that evokes a sense of security and institutional reliability.

## Colors

The palette is anchored by **Deep Indigo** (#2E3192), used exclusively for primary actions, active states, and brand-critical touchpoints to ensure high contrast and clear hierarchy.

- **Surfaces:** Use a range of Slates and Grays. Backgrounds stay at #F8FAFC, while containers and cards use pure #FFFFFF to pop against the subtle background.
- **Text:** Primary text should use Slate-900 for high legibility, with Slate-500 for secondary metadata.
- **AI Accents:** Use the Tertiary Blue (#0EA5E9) for AI-generated content, sparkle icons, or RAG-sourced citations to differentiate machine output from human input.
- **Statuses:** Semantic colors are applied with high-saturation icons but low-saturation background tints (e.g., Error text on a 10% opacity Red background).

## Typography

The design system utilizes **Inter** for all UI elements to ensure maximum legibility and a neutral, professional tone. Its tall x-height makes it ideal for dense document data.

- **Scale:** Use a strict 4px baseline grid. Headlines use tighter letter spacing (-0.02em) to feel more cohesive at larger sizes.
- **Mono Usage:** **JetBrains Mono** is reserved for metadata, document IDs, version numbers, and citations to provide a technical, "data-driven" feel that distinguishes facts from prose.
- **Hierarchy:** Use font weight rather than color to create hierarchy where possible (e.g., Medium weight for titles, Regular for body).

## Layout & Spacing

The layout utilizes a **12-column fluid grid** for main content areas and a **fixed-width sidebar** (280px) for navigation and document folder trees.

- **Rhythm:** All spacing must be a multiple of 4px. Use `lg` (24px) for major component gaps and `md` (16px) for internal padding within cards and chat bubbles.
- **Chat Interface:** The RAG chat window should be centered with a max-width of 800px to maintain line-length readability.
- **Reflow:** On mobile, sidebars collapse into a bottom sheet or "hamburger" menu. Margins shrink to 16px. Large data tables should transition to a card-stack view.

## Elevation & Depth

This design system uses a **Tonal Layering** approach combined with **Ambient Shadows** to create a structured hierarchy.

- **Level 0 (Base):** Background color (#F8FAFC).
- **Level 1 (Cards/Sidebar):** White (#FFFFFF) with a 1px border (#E2E8F0) and no shadow. Used for the main workspace.
- **Level 2 (Popovers/Dropdowns):** White with a soft, diffused shadow (0px 10px 15px -3px rgba(0, 0, 0, 0.05)).
- **Level 3 (Modals):** White with a deep shadow and a 20% opacity neutral backdrop blur (8px).

Shadows should never be pure black; always tint them with the primary or neutral-900 color to keep them feeling integrated into the UI.

## Shapes

The shape language is **Modern and Friendly**. 
- **Standard (8px):** Used for buttons, input fields, and small cards. 
- **Large (16px):** Used for main document containers, chat message groupings, and modal windows.
- **Pill:** Used exclusively for status badges (chips) and the main "New Chat" button to make them stand out as distinct interactive objects.

## Components

### Buttons
- **Primary:** Deep Indigo background, white text. No gradient. 8px corner radius.
- **Secondary:** White background, 1px Slate-200 border, Slate-700 text.
- **Ghost:** No border or background unless hovered. Used for secondary toolbar actions.

### Chat Bubbles
- **User:** Light Slate-100 background, dark text, right-aligned.
- **AI:** White background with a 1px Tertiary-200 border, left-aligned. Use a subtle blue glow or "sparkle" icon to denote RAG-sourced content.

### Inputs
- **Search/Chat Input:** Large (16px) rounded corners, 1px Slate-200 border. On focus, the border changes to Deep Indigo with a 3px soft indigo outer glow (ring).

### Document Cards
- Minimalist design. Features a file-type icon (colored by type), document name in Medium weight, and a "last modified" timestamp in JetBrains Mono.

### Chips/Badges
- Small, pill-shaped, with low-saturation backgrounds. Used for document tags (e.g., "Confidential," "PDF," "Draft").

### List Items
- Clean lines with 1px bottom borders. Hover states should use a subtle #F1F5F9 background transition.