"""Masking identifiers for DISPLAY (docs/security/pii-handling.md § Display).

Distinct from `app/core/logging.py`, which redacts for log sinks. That one is
allowed to be blunt — a redacted log line still does its job. This one is read
by a person who asked a question, so over-masking is a real cost: turning an
invoice number into asterisks makes the answer wrong-looking and unhelpful.

So it masks only values that **validate** as identifiers:

* PAN matches a specific five-letter/four-digit/one-letter shape.
* Aadhaar is twelve digits AND must pass the Verhoeff checksum. A bare twelve
  digit run — an order number, a phone with spaces — does not qualify.

This is not a security boundary. The admin can open the source document and see
the card. It prevents the number being read over a shoulder from a chat
transcript, which is exactly what the Display section asks for.
"""

from __future__ import annotations

import re

from app.modules.ai.identity_agent import valid_aadhaar, valid_pan

# Same shapes as the logging filter, matched here so they can be validated
# before anything is replaced.
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")


def mask_identifier(value: str, keep: int = 4) -> str:
    """``ABCDE1234F`` -> ``******234F``. Keeps the tail so a person can still
    confirm *which* card is meant without the number being usable."""
    digits = value.strip()
    if len(digits) <= keep:
        return "*" * len(digits)
    return "*" * (len(digits) - keep) + digits[-keep:]


def mask_for_display(text: str) -> str:
    """Mask validated PAN and Aadhaar numbers in free text."""
    if not text:
        return text

    def pan(match: re.Match[str]) -> str:
        found = match.group(0)
        return mask_identifier(found) if valid_pan(found) else found

    def aadhaar(match: re.Match[str]) -> str:
        found = match.group(0)
        # Validated before masking: a 12-digit invoice number is not an Aadhaar,
        # and masking it would make a correct answer look broken.
        return mask_identifier(re.sub(r"\D", "", found)) if valid_aadhaar(found) else found

    return _AADHAAR.sub(aadhaar, _PAN.sub(pan, text))


def contains_identifier(text: str) -> bool:
    """True when a validated identifier is present. Used to audit the fact that
    an answer touched identity data, without recording the value itself."""
    if not text:
        return False
    if any(valid_pan(m.group(0)) for m in _PAN.finditer(text)):
        return True
    return any(valid_aadhaar(m.group(0)) for m in _AADHAAR.finditer(text))
