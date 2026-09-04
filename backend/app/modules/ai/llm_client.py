"""The one place a model client is built (ADR-010 §4, ADR-002).

Supersedes ``gemini_client``. That module named its provider, which stopped
being true the moment a Super Admin could choose one.

Everything that talks to a model — the three agents and the three enrichment
stages — resolves its provider here, from the database, so a switch reaches all
of them at once. Nothing reads ``settings.GEMINI_*``.

Two contracts that look contradictory and are not:

* :func:`resolve` **raises**. Callers that must report a configuration problem —
  the admin API, ``/health`` — want the error.
* :func:`generate_json` and :func:`generate_text` **never raise**. They return
  ``None``, and their callers degrade. A missing provider must not fail a
  document that would otherwise index perfectly well (ADR-002).

The client itself is cached on the *identity* of the resolved config, so it is
rebuilt exactly when the version moves and never otherwise.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from app.core.exceptions import AppError, LLMConfigUnavailableError
from app.core.logging import get_logger
from app.modules.ai.llm_config import LLMConfig, get_config

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")

# How each provider names a model to CrewAI's LLM layer. The value is a prefix,
# and it is added in exactly ONE place - here.
_PROVIDER_PREFIX = {
    "gemini": "gemini",
    "openai": "openai",
    "anthropic": "anthropic",
    "azure": "azure",
}

# Prefixes stripped from a stored id before the right one is added. This is the
# guard against the defect that once produced "gemini/gemini/gemini-2.0-flash"
# and made BOTH AI paths fail silently into their fallbacks.
_STRIPPABLE = ("gemini/", "google/", "models/", "openai/", "anthropic/", "azure/")

# CrewAI 1.15 gives each provider a native client module that installs as its
# own extra. Only google-genai is in requirements.txt today, so registering a
# provider is not the same as being able to call it — this maps a provider to
# the extra that has to be installed first.
_PROVIDER_EXTRA = {
    "gemini": "crewai[google-genai]",
    "openai": "crewai[openai]",
    "anthropic": "crewai[anthropic]",
    "azure": "crewai[azure-ai-inference]",
}


def bare_model(model: str) -> str:
    """Strip any provider prefix, whichever one is present.

    Used on the write path so the database stores the bare id, and again in
    :func:`model_id` — because a value can also reach the database from a
    migration, a fixture or a hand-written SQL statement.
    """
    bare = (model or "").strip()
    lowered = bare.lower()
    for prefix in _STRIPPABLE:
        if lowered.startswith(prefix):
            bare = bare[len(prefix):]
            lowered = bare.lower()
    return bare


def model_id(provider: str, model: str) -> str:
    """Compose the provider-qualified model id.

    The prefix is added in exactly one place — here — and any existing one is
    removed first, so a doubled prefix is unrepresentable rather than merely
    unlikely.
    """
    bare = bare_model(model)

    prefix = _PROVIDER_PREFIX.get(provider)
    if prefix is None:
        # An enum column makes this unreachable from the database, but a
        # hand-built config in a test can still get here.
        raise ValueError(f"Unknown provider {provider!r}")

    return f"{prefix}/{bare}"


def resolve(session=None) -> LLMConfig:
    """The active configuration. Raises when there is none."""
    return get_config(session)


def is_configured() -> bool:
    """Whether a model call can be attempted at all. Never raises."""
    try:
        get_config()
        return True
    except AppError:
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("LLM configuration check failed",
                       extra={"error_type": type(exc).__name__})
        return False


def llm_kwargs(config: LLMConfig, temperature: float, timeout: int) -> dict[str, Any]:
    """Exactly what is handed to CrewAI.

    Separated from the construction so it can be asserted without installing
    every provider's SDK — see :func:`build_llm` for why that matters.
    """
    kwargs: dict[str, Any] = {
        "model": model_id(config.provider, config.model),
        "api_key": config.api_key,
        "temperature": temperature,
        "timeout": timeout,
    }
    # Only when set: passing base_url=None reaches some providers as an
    # override of their default endpoint rather than as an absence.
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return kwargs


@lru_cache(maxsize=8)
def build_llm(config: LLMConfig, temperature: float, timeout: int):
    """One client per (configuration, temperature).

    Keyed on the config object's identity — ``LLMConfig`` sets ``eq=False`` for
    exactly this. One object exists per version, so this is a hit while the
    version holds and a miss the moment it moves. No invalidation call is
    needed anywhere, which is the point: an invalidation that must be
    remembered is one that will eventually be forgotten.
    """
    from crewai import LLM

    try:
        return LLM(**llm_kwargs(config, temperature, timeout))
    except ImportError as exc:
        # CrewAI 1.15 dispatches each provider to a native module that ships as
        # a separate extra; only google-genai is installed. Without this the
        # failure surfaces as a bare ImportError from deep inside crewai, which
        # reads like a broken install rather than a provider that was never
        # provisioned.
        logger.error(
            "The active provider's client library is not installed",
            extra={"provider": config.provider,
                   "install_extra": _PROVIDER_EXTRA.get(config.provider, "?")},
        )
        raise LLMConfigUnavailableError() from exc


def clean_json(raw: str) -> Any:
    """Salvage JSON from a model response.

    Models wrap JSON in markdown fences and occasionally leave trailing commas.
    On failure this returns None rather than raising - an unparseable response
    is a degraded stage, not a failed document.
    """
    if not raw:
        return None

    text = _FENCE.sub("", raw).strip()
    text = _TRAILING_COMMA.sub(r"\1", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced object or array in the response.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    logger.warning("Could not parse model response as JSON")
    return None


def generate_text(
    prompt: str, *, temperature: float = 0.2, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> str | None:
    """One completion. Returns None on any failure, including no provider."""
    try:
        config = get_config()
    except AppError as exc:
        logger.info("No usable LLM configuration; caller will use its fallback",
                    extra={"error_code": str(exc.error_code)})
        return None

    try:
        # Never log the prompt or the exception message: both can contain
        # document text (spec §39).
        raw = build_llm(config, temperature, timeout).call(prompt)
    except Exception as exc:
        logger.warning("Model call failed",
                       extra={"error_type": type(exc).__name__,
                              "provider": config.provider})
        return None

    return (raw or "").strip() or None


def generate_json(
    prompt: str, *, temperature: float = 0.0, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> Any | None:
    """Return parsed JSON, or None if unavailable or unparseable."""
    raw = generate_text(prompt, temperature=temperature, timeout=timeout)
    if raw is None:
        return None
    return clean_json(raw)


def describe() -> dict[str, Any]:
    """Diagnostics. Never the credential itself."""
    from app.modules.ai import llm_config

    described = llm_config.describe()
    if described.get("configured"):
        described["resolved_model_id"] = model_id(
            described["provider"], described["model"]
        )
    return described
