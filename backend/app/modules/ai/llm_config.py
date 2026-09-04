"""Resolves the active LLM configuration (ADR-010 §3-§5).

Every agent, in every process, calls :func:`get_config` before it builds a
client. The API and the Celery worker are separate long-lived processes and do
not coordinate: each one checks a version integer against the database and
rebuilds only when it moved. That is the whole synchronisation mechanism, and it
is why switching a provider needs no restart, no broker message and no pub/sub.

Three properties are load-bearing:

**One statement.** The version and the configuration it labels come from the
same row in the same snapshot. Split across two reads, a process can cache
version 6 holding the configuration as it was at version 5 — a stale config
stamped with the current version, which therefore never rebuilds.

**A config, not a client.** ``crewai.LLM`` and ``google.generativeai`` are
unrelated types; one cache slot fits neither. Call sites cache their own client
keyed on the object this module returns.

**A database that cannot be read has not changed.** A brief outage serves the
last known good configuration; a long one refuses. See :data:`_CACHE`.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.exceptions import LLMConfigUnavailableError, NoActiveLLMProviderError
from app.core.logging import get_logger
from config.settings import settings

logger = get_logger(__name__)

# One row, one snapshot. Ordering by version is defensive: the partial unique
# index already permits only one active row, and if that ever ceased to be true
# the newest configuration is the right one to prefer over an arbitrary one.
_ACTIVE_PROVIDER_SQL = text(
    """
    SELECT config_version, provider_name, model_name,
           encrypted_api_key, encryption_key_id, base_url, config, key_fingerprint
    FROM llm_providers
    WHERE is_active
    ORDER BY config_version DESC
    LIMIT 1
    """
)


@dataclass(frozen=True, eq=False)
class LLMConfig:
    """A resolved provider configuration.

    ``eq=False`` is deliberate, and it is what makes the call-site caches work.
    Identity comparison means one object per version, so an ``lru_cache(1)``
    keyed on this object is a hit for as long as the version holds and a miss
    the moment it moves. Value equality would need a hashable ``params``, and a
    dataclass holding a dict cannot be hashed at all.
    """

    version: int
    provider: str
    model: str
    api_key: str  # decrypted; memory only, never logged or returned
    base_url: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def __repr__(self) -> str:
        """Never renders the credential — this object reaches log lines."""
        return (
            f"<LLMConfig v{self.version} {self.provider}/{self.model} "
            f"key={self.fingerprint}>"
        )


# The cache. One entry, because one provider is active.
#
# ``confirmed_at`` is a monotonic timestamp of the last SUCCESSFUL read, not of
# the last attempt and not a wall clock: an NTP correction must not be able to
# widen or shrink the staleness window.
_CACHE: dict[str, Any] = {"config": None, "confirmed_at": 0.0, "stale": False}
_LOCK = threading.Lock()


def get_config(session: Session | None = None) -> LLMConfig:
    """The active configuration, rebuilt only when its version has moved.

    Pass ``session`` wherever the caller already has one — the API request path
    always does. It avoids a second pooled connection, and under READ COMMITTED
    the read sees everything committed before this statement regardless of when
    the caller's transaction began. The worker mostly has no session at the
    moment it needs a client, so one is opened and closed here.
    """
    try:
        row = _read_active_row(session)
    except SQLAlchemyError as exc:
        return _serve_last_known_good(exc)

    if row is None:
        # The database answered, and the answer is that nothing is configured.
        # Drop the cached config: a provider that was deactivated must not come
        # back to life through a later outage.
        with _LOCK:
            _CACHE.update(config=None, stale=False)
        raise NoActiveLLMProviderError()

    cached: LLMConfig | None = _CACHE["config"]
    now = time.monotonic()

    if cached is not None and cached.version == row.config_version:
        with _LOCK:
            _CACHE.update(confirmed_at=now, stale=False)
        return cached

    # Build completely, then swap. A reader never observes a half-built config.
    resolved = _build(row)
    with _LOCK:
        _CACHE.update(config=resolved, confirmed_at=now, stale=False)

    if cached is not None:
        logger.info(
            "LLM configuration changed",
            extra={
                "from_version": cached.version,
                "to_version": resolved.version,
                "provider": resolved.provider,
                "model": resolved.model,
            },
        )
    return resolved


def describe() -> dict[str, Any]:
    """What ``/health`` reports. Never the credential.

    Reads the cache only — deliberately does not hit the database, so a health
    check cannot itself become the thing that fails.
    """
    config: LLMConfig | None = _CACHE["config"]
    if config is None:
        return {"provider": None, "model": None, "configured": False}

    return {
        "provider": config.provider,
        "model": config.model,
        "key_fingerprint": config.fingerprint,
        "config_version": config.version,
        "config_confirmed_age_s": round(time.monotonic() - _CACHE["confirmed_at"], 1),
        "degraded": bool(_CACHE["stale"]),
        "configured": True,
    }


def is_serving_stale() -> bool:
    """True while answers are being produced from an unconfirmed configuration.

    This is what sets ``degraded`` on a chat response.
    """
    return bool(_CACHE["stale"])


def reset_cache() -> None:
    """Forget everything. For tests and for a deliberate reload."""
    with _LOCK:
        _CACHE.update(config=None, confirmed_at=0.0, stale=False)


# --- internals -------------------------------------------------------------- #


def _read_active_row(session: Session | None):
    if session is not None:
        return session.execute(_ACTIVE_PROVIDER_SQL).one_or_none()

    from app.core.database import SessionLocal

    own = SessionLocal()
    try:
        return own.execute(_ACTIVE_PROVIDER_SQL).one_or_none()
    finally:
        own.close()


def build_config(row) -> LLMConfig:
    """Resolve any provider row, active or not, without touching the cache.

    This is how a provider is tested before it is activated: the connection
    check has to run against a row that nothing is answering from yet.
    """
    return _build(row)


def _build(row) -> LLMConfig:
    """Decrypt and assemble.

    A decryption failure is **not** an availability problem — the database was
    readable and answered. It means the wrong key, an altered row, or a key id
    no longer configured, and none of those improve by waiting or by serving an
    older configuration. It fails immediately.
    """
    try:
        api_key = crypto.decrypt(row.encrypted_api_key)
    except crypto.CryptoError as exc:
        logger.error(
            "Active LLM credential could not be decrypted",
            extra={
                "provider": row.provider_name,
                "encryption_key_id": row.encryption_key_id,
                "error_type": type(exc).__name__,
            },
        )
        raise LLMConfigUnavailableError() from exc

    return LLMConfig(
        version=row.config_version,
        provider=row.provider_name,
        model=row.model_name,
        api_key=api_key,
        base_url=row.base_url,
        params=dict(row.config or {}),
        fingerprint=row.key_fingerprint,
    )


def _serve_last_known_good(exc: Exception) -> LLMConfig:
    """The read failed. Decide between availability and safety.

    The failures this covers are overwhelmingly brief and have nothing to do
    with configuration: pool exhaustion, a connection closed while idle, a
    restart or a failover. In none of them has the configuration changed — a
    database that cannot be read is not a database that has changed.

    What bounds the risk is that this branch is only reachable while the
    database is unreachable. Whenever it is reachable, this module and a
    fail-closed one behave identically, and a Super Admin's deactivation is a
    write to that same database.
    """
    cached: LLMConfig | None = _CACHE["config"]

    if cached is None:
        logger.error(
            "LLM configuration is unreadable and nothing is cached",
            extra={"error_type": type(exc).__name__},
        )
        raise LLMConfigUnavailableError() from exc

    age = time.monotonic() - _CACHE["confirmed_at"]
    ceiling = settings.LLM_CONFIG_STALENESS_SECONDS

    if age > ceiling:
        logger.error(
            "Cached LLM configuration is older than the staleness ceiling",
            extra={
                "age_seconds": round(age, 1),
                "ceiling_seconds": ceiling,
                "error_type": type(exc).__name__,
            },
        )
        raise LLMConfigUnavailableError() from exc

    with _LOCK:
        _CACHE["stale"] = True

    logger.warning(
        "Serving the last known good LLM configuration",
        extra={
            "age_seconds": round(age, 1),
            "ceiling_seconds": ceiling,
            "config_version": cached.version,
            "error_type": type(exc).__name__,
        },
    )
    return cached
