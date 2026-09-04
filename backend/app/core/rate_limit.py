"""Redis-backed fixed-window rate limiting (spec §40).

A fixed window, not a sliding one. It allows up to 2x the limit across a window
boundary — the classic fixed-window flaw — and that is accepted here: these
limits exist to stop credential stuffing and runaway loops, not to meter a paid
API to the request. A sliding window costs a sorted set per caller and more
Redis round-trips for a precision nothing here needs.

**Fails open.** If Redis is unreachable the request is allowed. A cache outage
should not take the API down with it; the alternative — failing closed — turns
one dependency being down into a total outage, which is the worse failure for a
single-admin internal system. The choice is logged so it is visible.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis

from app.cache.redis_client import get_redis
from app.core.logging import get_logger

logger = get_logger(__name__)

_PREFIX = "ratelimit:"


@dataclass(frozen=True)
class Rule:
    """A parsed `"5/minute"` style limit."""

    limit: int
    window_seconds: int
    scope: str  # "ip" or "user"

    @property
    def retry_after(self) -> int:
        return self.window_seconds


@dataclass
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


_UNITS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86_400,
}


def parse_rule(value: str, *, scope: str) -> Rule:
    """Parse `"20/minute"`. Raises on nonsense — a mistyped limit in config
    should fail at startup, not silently become no limit at all."""
    raw = (value or "").strip().lower()
    count, separator, unit = raw.partition("/")

    # A bare "5" is ambiguous. Defaulting it to per-minute is precisely the
    # silent reinterpretation this function exists to prevent.
    if not separator:
        raise ValueError(f"Rate limit '{value}' must name a window, e.g. '5/minute'.")

    unit = unit.strip().rstrip("s")
    if unit not in _UNITS:
        raise ValueError(f"Unknown rate-limit window '{unit}' in '{value}'.")

    try:
        limit = int(count)
    except ValueError as exc:
        raise ValueError(f"Invalid rate-limit count in '{value}'.") from exc

    if limit < 1:
        raise ValueError(f"Rate limit must be at least 1, got '{value}'.")

    return Rule(limit=limit, window_seconds=_UNITS[unit], scope=scope)


def check(key: str, rule: Rule) -> Decision:
    """Count this request against `key`. Never raises."""
    try:
        client = get_redis()
        # INCR then EXPIRE only on the first hit: re-expiring on every request
        # would slide the window forward and the limit would never reset for a
        # caller that keeps hitting it.
        current = client.incr(f"{_PREFIX}{key}")
        if current == 1:
            client.expire(f"{_PREFIX}{key}", rule.window_seconds)
            ttl = rule.window_seconds
        else:
            ttl = client.ttl(f"{_PREFIX}{key}")
            if ttl is None or ttl < 0:
                # Key exists without a TTL — only possible if EXPIRE failed.
                # Repair it rather than leaving a counter that never resets.
                client.expire(f"{_PREFIX}{key}", rule.window_seconds)
                ttl = rule.window_seconds
    except redis.RedisError as exc:
        from app.cache.redis_client import _mark_unavailable

        _mark_unavailable()
        logger.warning(
            "Rate limiting unavailable; allowing the request",
            extra={"error": str(exc), "rate_limit_key": key},
        )
        return Decision(allowed=True, limit=rule.limit, remaining=rule.limit,
                        retry_after=0)

    remaining = max(0, rule.limit - current)
    return Decision(
        allowed=current <= rule.limit,
        limit=rule.limit,
        remaining=remaining,
        retry_after=int(ttl),
    )


def reset(key: str) -> None:
    """Clear one counter. Used by tests, never on a request path."""
    try:
        get_redis().delete(f"{_PREFIX}{key}")
    except redis.RedisError:
        pass
