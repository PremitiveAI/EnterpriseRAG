"""Redis client and the refresh-token denylist.

Redis is a cache and a broker. It is never a source of truth (spec §9).
"""

from __future__ import annotations

import time

import redis

from app.core.logging import get_logger
from config.settings import settings

logger = get_logger(__name__)

_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL, decode_responses=True, socket_connect_timeout=3, socket_timeout=3
)

# --- Circuit breaker ---------------------------------------------------- #
#
# Every caller here already degrades gracefully when Redis is down - the rate
# limiter allows the request, the chat context rebuilds from PostgreSQL. What
# they do NOT survive is the COST of finding out: a 3-second connect timeout,
# several times per request.
#
# So a failure is remembered briefly and subsequent calls fail fast instead of
# re-timing-out. An outage then costs one timeout every COOLDOWN seconds rather
# than one per call - the difference between a degraded system and an unusable
# one.

_COOLDOWN_SECONDS = 10.0
_unavailable_until = 0.0


def _mark_unavailable() -> None:
    global _unavailable_until
    _unavailable_until = time.monotonic() + _COOLDOWN_SECONDS


def _mark_available() -> None:
    global _unavailable_until
    _unavailable_until = 0.0


def is_circuit_open() -> bool:
    """True while a recent failure is still being remembered."""
    return time.monotonic() < _unavailable_until


class _FastFailRedis:
    """Stands in for the client while the circuit is open.

    Raises the same exception the real client would, immediately, so every
    caller's existing ``except redis.RedisError`` path runs unchanged.
    """

    def __getattr__(self, name: str):
        def fail(*_args, **_kwargs):
            raise redis.ConnectionError("Redis is unavailable (circuit open)")

        return fail


def get_redis():
    if is_circuit_open():
        return _FastFailRedis()
    return redis.Redis(connection_pool=_pool)


def ping() -> bool:
    """Liveness check. Also the only place the circuit is closed again."""
    try:
        alive = bool(redis.Redis(connection_pool=_pool).ping())
        if alive:
            _mark_available()
        return alive
    except redis.RedisError as exc:
        _mark_unavailable()
        logger.warning("Redis unavailable", extra={"error": str(exc)})
        return False


# --- Refresh-token denylist -------------------------------------------- #
# Logout must take effect immediately rather than at token expiry, so revoked
# refresh jtis are held until their natural expiry.

_DENY_PREFIX = "auth:denylist:"


def revoke_refresh_jti(jti: str, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    try:
        get_redis().setex(f"{_DENY_PREFIX}{jti}", ttl_seconds, "1")
    except redis.RedisError as exc:
        _mark_unavailable()
        # Fail loudly in the log: a revocation that did not persist means a
        # logged-out token still works until it expires.
        logger.error("Failed to revoke refresh token", extra={"error": str(exc)})


def is_refresh_jti_revoked(jti: str) -> bool:
    try:
        return get_redis().exists(f"{_DENY_PREFIX}{jti}") == 1
    except redis.RedisError as exc:
        _mark_unavailable()
        # Fail closed: if the denylist cannot be consulted, treat the token as
        # revoked rather than silently honouring one that may have been.
        logger.error("Denylist unavailable; refusing refresh", extra={"error": str(exc)})
        return True
