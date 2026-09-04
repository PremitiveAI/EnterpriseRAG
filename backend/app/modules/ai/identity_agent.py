"""Agent 1 — Identity Document (ADR-002).

Recognises PAN and Aadhaar cards from OCR text and extracts their fields.

Runs through CrewAI like agents 2 and 3, resolving its provider from the
database (ADR-010). It does NOT perform OCR: Tesseract produces the text, this
agent reasons over it. Recognition is reasoning; OCR is not (spec §8, ADR-004).

Expect noisy input. Identity cards are dense, coloured and usually photographed
at an angle — Tesseract's hardest case. The prompt therefore instructs the
model to return null rather than guess: a confidently wrong Aadhaar number is
far worse than a missing one.

🔴 Extracted values are sensitive personal identifiers. They are validated
structurally, and never written to a log or an audit row
(docs/security/pii-handling.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.modules.ai import crew

logger = get_logger(__name__)

# Agent 1's persona. Agents 2 and 3 have carried one since ADR-002; this one
# used to be a bare prompt against a provider-specific SDK, which is why a
# provider switch would have reached two agents out of three (ADR-010).
ROLE = "Identity Document Analyst"
GOAL = (
    "Recognise Indian identity documents from noisy OCR text and extract only "
    "the fields that can be read with confidence."
)
BACKSTORY = (
    "You have spent years reading photographed identity cards that scanners "
    "mangle. You know that a confidently wrong identifier is far worse than a "
    "missing one, and you would rather return null than guess."
)

# Zero, not the composer's 0.2. This stage extracts identifiers; there is
# nothing here that creativity improves.
TEMPERATURE = 0.0

# Generous next to the chat agents' budgets, because nothing is waiting on it:
# agent 1 runs in the Celery worker, off the request path.
TIMEOUT_SECONDS = 45

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_RE = re.compile(r"^\d{12}$")

IDENTITY_TYPES = {"pan_card", "aadhaar_card", "passport", "driving_licence", "voter_id"}

# Verhoeff tables — Aadhaar's checksum. Structural validation is deterministic
# and belongs outside the agent.
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(number: str) -> bool:
    if not number.isdigit():
        return False
    check = 0
    for i, digit in enumerate(reversed(number)):
        check = _D[check][_P[i % 8][int(digit)]]
    return check == 0


def valid_pan(value: str) -> bool:
    return bool(PAN_RE.match(value.strip().upper()))


def valid_aadhaar(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return bool(AADHAAR_RE.match(digits)) and verhoeff_valid(digits)


@dataclass
class IdentityResult:
    document_type: str | None = None
    confidence: float = 0.0
    fields: dict[str, str] = field(default_factory=dict)
    discarded: list[str] = field(default_factory=list)


def _validate(raw: dict[str, object]) -> tuple[dict[str, str], list[str]]:
    """Keep only fields that pass their structural check.

    A field that fails is DISCARDED, not stored. An invalid identifier that
    looks authoritative will be trusted, which is worse than a gap.
    """
    kept: dict[str, str] = {}
    discarded: list[str] = []

    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()

        if key == "pan_number":
            if valid_pan(value):
                kept[key] = value.upper()
            else:
                discarded.append(key)
        elif key in {"aadhaar_number", "aadhar_number"}:
            if valid_aadhaar(value):
                kept["aadhaar_number"] = re.sub(r"\D", "", value)
            else:
                discarded.append("aadhaar_number")
        else:
            kept[key] = value[:255]

    return kept, discarded


def analyse(ocr_text: str) -> IdentityResult:
    """Identify an identity document and extract its fields.

    Returns an empty result when no provider is active or the response is
    unusable — the document is still classified and indexed normally.
    """
    if not ocr_text.strip() or not crew.is_available():
        return IdentityResult()

    prompt = f"""You are reading text produced by OCR from a photographed identity document.
The text is likely to be noisy, partially garbled, and out of order.

Identify the document type and extract its fields.

Respond with JSON only:
{{"document_type": "pan_card|aadhaar_card|passport|driving_licence|voter_id|not_an_identity_document",
  "confidence": 0.0,
  "fields": {{"name": null, "father_name": null, "date_of_birth": null,
              "pan_number": null, "aadhaar_number": null, "gender": null, "address": null}}}}

Rules:
- Use null for any field you cannot read with confidence. NEVER guess a number.
- A wrong identifier is far worse than a missing one.
- If this is not an identity document, say so and leave every field null.

OCR text:
\"\"\"
{ocr_text[:3000]}
\"\"\""""

    result = crew.run_json(
        role=ROLE,
        goal=GOAL,
        backstory=BACKSTORY,
        task=prompt,
        expected_output=(
            'JSON only: {"document_type": "...", "confidence": 0.0, "fields": {...}}'
        ),
        timeout=TIMEOUT_SECONDS,
        temperature=TEMPERATURE,
    )
    if not isinstance(result, dict):
        return IdentityResult()

    document_type = result.get("document_type")
    if not isinstance(document_type, str) or document_type not in IDENTITY_TYPES:
        return IdentityResult()

    try:
        confidence = float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    raw_fields = result.get("fields")
    fields, discarded = _validate(raw_fields if isinstance(raw_fields, dict) else {})

    # Field NAMES only. Values are never logged (docs/security/pii-handling.md).
    logger.info(
        "Identity document recognised",
        extra={
            "document_type": document_type,
            "confidence": round(confidence, 2),
            "fields_kept": sorted(fields),
            "fields_discarded": sorted(discarded),
        },
    )

    return IdentityResult(
        document_type=document_type,
        confidence=confidence,
        fields=fields,
        discarded=discarded,
    )
