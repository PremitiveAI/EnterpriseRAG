"""Superseded by :mod:`app.modules.ai.llm_client` (ADR-010).

The name stopped being true when a Super Admin gained the ability to choose the
provider: this module hard-coded Gemini, read ``settings.GEMINI_API_KEY``
directly, and built a ``google.generativeai`` client that no other provider
could satisfy.

Kept only so an import missed during the migration fails loudly at the call
rather than silently answering from a provider nobody selected. As of phase 7
``GEMINI_API_KEY`` has left the settings model, so nothing here could work even
if it were called — this file can be deleted whenever its owner says so.

Nothing in the application imports this. ``clean_json`` moved to ``llm_client``
unchanged and is re-exported here for any test that still reaches for it.
"""

from __future__ import annotations

from typing import Any

from app.modules.ai.llm_client import clean_json  # noqa: F401  (re-export)

__all__ = ["clean_json"]

_GONE = (
    "app.modules.ai.gemini_client is superseded by app.modules.ai.llm_client. "
    "The provider now comes from the database, not from GEMINI_API_KEY (ADR-010)."
)


def is_configured() -> bool:
    raise RuntimeError(_GONE)


def generate_json(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(_GONE)


def generate_text(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(_GONE)
