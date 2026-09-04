"""Security response headers (docs/security/security-model.md).

The API serves JSON, not HTML, so the headers that matter most here are the
ones that stop a browser doing something clever with a response it should treat
as inert data.

`Strict-Transport-Security` is production-only: sending it over plain HTTP on
localhost would pin the developer's browser to HTTPS for a host that does not
serve it, and that pin outlives the mistake.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from config.settings import settings

# `frame-ancestors 'none'` rather than only X-Frame-Options: the header is
# legacy, the directive is the one modern browsers honour.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # This API is called by a first-party frontend; no page here needs any of
    # these, so they are switched off rather than left at browser defaults.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        for header, value in BASE_HEADERS.items():
            response.headers.setdefault(header, value)

        # The interactive docs are HTML and load their own scripts and styles;
        # a `default-src 'none'` policy would render them blank. They are
        # disabled in production anyway.
        if not _is_docs(request.url.path):
            response.headers.setdefault("Content-Security-Policy", API_CSP)

        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        return response


def _is_docs(path: str) -> bool:
    return path in {"/docs", "/redoc", "/docs/oauth2-redirect"}
