"""Authentication middleware.

Public paths are an EXACT-MATCH set, never a prefix test.

A ``startswith()`` exemption written for one path also exempts every route
sharing that prefix. That exact bug exists in a sibling project in this
codebase and left ten endpoints reachable with no token
(docs/security/security-model.md).
"""

from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import get_logger, user_id_var
from app.modules.auth.services.auth_service import resolve_principal
from config.settings import settings

logger = get_logger(__name__)

PUBLIC_PATHS: frozenset[str] = frozenset({
    "/health",
    f"{settings.API_V1_PREFIX}/auth/login",
    f"{settings.API_V1_PREFIX}/auth/refresh",
    # Release 2: an Organization Admin has no token until they verify a code.
    f"{settings.API_V1_PREFIX}/auth/otp/request",
    f"{settings.API_V1_PREFIX}/auth/otp/verify",
})

# Only outside production; main.py disables the docs entirely when production.
_DOC_PATHS: frozenset[str] = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})


# The public chatbot endpoints carry an organization id in the path, so they
# cannot be exact-match entries. They are FULLY ANCHORED patterns requiring a
# UUID - not a prefix test.
#
# The difference matters: `startswith("/api/v1/public")` would also exempt any
# future route sharing that prefix, which is precisely the bug that left ten
# endpoints unauthenticated in a sibling project. These patterns match one shape
# and nothing else - a trailing segment, a different verb path, or a non-UUID
# organization all fail to match and fall through to authentication.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

PUBLIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"^{re.escape(settings.API_V1_PREFIX)}/public/{_UUID}/config$"),
    re.compile(rf"^{re.escape(settings.API_V1_PREFIX)}/public/{_UUID}/chat$"),
)


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if any(pattern.fullmatch(path) for pattern in PUBLIC_PATTERNS):
        return True
    if not settings.is_production and path in _DOC_PATHS:
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" or _is_public(request.url.path):
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")

        if scheme.lower() != "bearer" or not token:
            return _unauthorized(request, ErrorCode.UNAUTHORIZED, "Authentication required.")

        db = SessionLocal()
        try:
            principal = resolve_principal(db, token)
        except AppError as exc:
            return _unauthorized(request, exc.error_code, exc.message, exc.status_code)
        finally:
            db.close()

        request.state.principal = principal
        # Kept for Release 1 code paths that still read user_id.
        request.state.user_id = principal.subject_id
        request.state.user_email = principal.email
        request.state.organization_id = principal.organization_id
        user_id_var.set(str(principal.subject_id))

        return await call_next(request)


def _unauthorized(
    request: Request, code: ErrorCode, message: str, status: int = 401
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error_code": str(code),
            "message": message,
            "details": {},
            "request_id": getattr(request.state, "request_id", None),
        },
    )
