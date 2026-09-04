"""Rate-limit middleware (spec §40, docs/security/security-model.md).

Runs INSIDE AuthMiddleware, so `request.state.user_id` is already set and a
per-user limit can key on the real identity rather than on an IP behind a NAT.
That ordering also means an unauthenticated request gets 401, not 429 — the
caller learns the real problem.

Login is keyed by **IP**, everything else by **user**. The account field on a
login attempt is attacker-controlled, so keying login by user would let an
attacker reset their own limit by changing the email.
"""

from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.error_codes import ErrorCode
from app.core.logging import get_logger
from app.core.rate_limit import Rule, check, parse_rule
from config.settings import settings

logger = get_logger(__name__)

API = settings.API_V1_PREFIX

LOGIN_PATH = f"{API}/auth/login"
UPLOAD_PATH = f"{API}/admin/documents/upload"
# The only path segment that varies is the conversation id.
MESSAGES_PATTERN = re.compile(
    rf"^{re.escape(API)}/chat/conversations/[^/]+/messages$"
)

# Never counted: a health probe every second would otherwise consume the
# default budget and lock out the operator watching it.
EXEMPT_PATHS: frozenset[str] = frozenset({
    "/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect",
})


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        # Parsed once at startup. A mistyped limit in .env raises here rather
        # than silently becoming no limit at all.
        self.login: Rule = parse_rule(settings.RATE_LIMIT_LOGIN, scope="ip")
        self.upload: Rule = parse_rule(settings.RATE_LIMIT_UPLOAD, scope="user")
        self.chat: Rule = parse_rule(settings.RATE_LIMIT_CHAT, scope="user")
        self.default: Rule = parse_rule(settings.RATE_LIMIT_DEFAULT, scope="user")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        rule, bucket = self._rule_for(request)
        identity = self._identity(request, rule)
        if identity is None:
            # No usable key — an unauthenticated request to a per-user bucket.
            # Auth has already rejected it, or will.
            return await call_next(request)

        decision = check(f"{bucket}:{identity}", rule)

        if not decision.allowed:
            logger.warning(
                "Rate limit exceeded",
                extra={"bucket": bucket, "path": request.url.path, "limit": rule.limit},
            )
            return _too_many(request, decision.retry_after, rule.limit)

        response = await call_next(request)
        # Advertised on every response, so a client can back off before being
        # refused rather than discovering the limit by hitting it.
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response

    # ------------------------------------------------------------------ #

    def _rule_for(self, request: Request) -> tuple[Rule, str]:
        path = request.url.path

        if request.method == "POST":
            if path == LOGIN_PATH:
                return self.login, "login"
            if path == UPLOAD_PATH:
                return self.upload, "upload"
            if MESSAGES_PATTERN.match(path):
                return self.chat, "chat"

        return self.default, "default"

    @staticmethod
    def _identity(request: Request, rule: Rule) -> str | None:
        if rule.scope == "ip":
            return _client_ip(request) or "unknown"

        user_id = getattr(request.state, "user_id", None)
        return str(user_id) if user_id else None


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First entry is the original client. Spoofable without a trusted
        # proxy in front — accepted, and recorded in the security model.
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _too_many(request: Request, retry_after: int, limit: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "error_code": str(ErrorCode.RATE_LIMIT_EXCEEDED),
            "message": "Too many requests. Try again shortly.",
            "details": {"limit": limit, "retry_after_seconds": retry_after},
            "request_id": getattr(request.state, "request_id", None),
        },
        headers={"Retry-After": str(max(1, retry_after))},
    )
