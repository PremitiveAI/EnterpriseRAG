# ADR-004 — Tesseract for OCR

**Status:** Accepted · **Date:** 2026-08-21 · **Decides:** spec §10, §22

## Decision

**Tesseract OCR**, installed natively on the host, invoked through `pytesseract`.

## When OCR runs

OCR is **conditional**, not universal:

| Input | Path |
| ----- | ---- |
| JPG / JPEG / PNG / WEBP | Always OCR — an image has no text layer |
| PDF with an extractable text layer | **No OCR.** PyMuPDF returns the text directly |
| PDF with no text layer (a scan) | OCR each page after rasterising |
| DOCX / PPTX / TXT | Never — text is structured already |

The trigger for a PDF is: extracted text below a configurable character threshold per page
(`OCR_MIN_CHARS_PER_PAGE`, default 50) ⇒ treat that page as scanned.

## Consequences

- **External binary dependency.** Tesseract must be installed and on `PATH`, or
  `TESSERACT_CMD` set. It is not a pip package; `pip install` alone will not make OCR work.
  Startup performs a capability check and logs a clear warning if it is missing.
- Slowest stage in the pipeline by an order of magnitude — seconds per page. This is the primary
  reason §20 forbids extraction on the request path.
- Accuracy is materially worse than cloud OCR on low-resolution scans, skewed pages and
  stylised card layouts. **Identity cards are exactly that hard case** — PAN and Aadhaar cards
  are dense, coloured, and often photographed at an angle. Expect agent 1
  ([ADR-002](ADR-002-crewai-three-agents.md)) to receive noisy input and prompt it accordingly.
- Preprocessing (greyscale, deskew, threshold via OpenCV) measurably improves results and is
  part of the OCR service, not an afterthought.
- Language packs are per-language installs. `eng` only in v1.

## Alternatives rejected

**Gemini vision.** No install, better on hard scans, already a dependency — but per-page cost on
every scanned document. Rejected by the user in favour of a free local engine.
