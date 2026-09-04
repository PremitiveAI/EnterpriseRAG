"""Security properties asserted against the running application.

The route-enumeration test is the important one here. It walks the app's OWN
route table, so a route added later cannot silently become public — the failure
mode that left ten endpoints unauthenticated in a sibling project
(docs/security/security-model.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import make_tenant, otp_login, reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Every path that may answer without a token, and why.
EXPECTED_PUBLIC = {
    "/health",                    # liveness probe
    "/api/v1/auth/login",         # has no token yet, by definition
    "/api/v1/auth/refresh",       # authenticates via refresh cookie + denylist
    # Interactive docs, disabled in production by main.py.
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
    # Release 2: an Organization Admin has no token until they verify a code,
    # so both OTP endpoints are public by definition.
    "/api/v1/auth/otp/request",
    "/api/v1/auth/otp/verify",
}

# A body good enough to get past request validation, so a 422 can never be
# mistaken for "the route rejected me".
SAMPLE_BODIES: dict[str, dict] = {
    "POST": {"content": "hello", "title": "x", "email": "a@example.com",
             "password": "x" * 12, "identifier": "a@example.com", "otp": "1111"},
    "PATCH": {"title": "x"},
}


@pytest.fixture(scope="module", autouse=True)
def _schema():
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture()
def db():
    from app.core.database import SessionLocal

    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _clean(db):
    reset_database(db)
    _flush_rate_limits()
    yield


def _flush_rate_limits():
    """Counters are shared state; a previous test must not exhaust this one."""
    try:
        from app.cache.redis_client import get_redis

        client = get_redis()
        for key in client.scan_iter("ratelimit:*"):
            client.delete(key)
    except Exception:
        pass


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def tenant(db):
    return make_tenant(db, email="orgadmin@example.com")


@pytest.fixture()
def org_auth(client, tenant):
    """An Organization Admin - the subject tenant routes require."""
    return otp_login(client, "orgadmin@example.com")


@pytest.fixture()
def auth(client, db):
    from app.modules.auth.services.auth_service import AuthService

    AuthService(db).create_admin(
        email="admin@example.com", password="a-long-test-password", full_name="Admin"
    )
    r = client.post("/api/v1/auth/login",
                    json={"email": "admin@example.com", "password": "a-long-test-password"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def registered_routes() -> list[tuple[str, str]]:
    """(method, path) for every route the app serves.

    Read from the OpenAPI schema rather than `app.routes`: FastAPI 0.141 keeps
    included routers as `_IncludedRouter` objects, so walking `app.routes`
    naively finds only the four routes defined on the app itself — and a test
    that enumerates almost nothing passes while proving nothing.
    """
    from app.main import app

    pairs: list[tuple[str, str]] = []
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                pairs.append((method.upper(), path))

    # The docs pages are not in the schema; add them so the public set is
    # asserted in full rather than partially.
    pairs.extend([("GET", "/docs"), ("GET", "/redoc"), ("GET", "/docs/oauth2-redirect")])
    return pairs


def _error_code(response) -> str | None:
    try:
        return response.json().get("error_code")
    except Exception:
        return None


def call(client, method: str, path: str, **kwargs):
    concrete = (
        path.replace("{document_id}", "00000000-0000-4000-8000-000000000000")
            .replace("{conversation_id}", "00000000-0000-4000-8000-000000000000")
    )
    return client.request(method, concrete, json=SAMPLE_BODIES.get(method), **kwargs)


# --- The enumeration test ------------------------------------------------ #


def test_the_route_table_is_not_empty():
    """Guards the guard: an enumeration test over zero routes proves nothing."""
    routes = registered_routes()
    assert len(routes) >= 15, routes


def test_every_route_requires_a_token_except_the_public_set(client):
    unprotected: list[tuple[str, str, int, str]] = []

    for method, path in registered_routes():
        if path in EXPECTED_PUBLIC:
            continue
        response = call(client, method, path)
        # 401 AND the middleware's own code: a 401 for some other reason would
        # otherwise let a genuinely public route pass this test.
        if response.status_code != 401 or _error_code(response) != "UNAUTHORIZED":
            unprotected.append((method, path, response.status_code, _error_code(response)))

    assert unprotected == [], f"reachable without a token: {unprotected}"


def test_the_public_set_matches_what_is_actually_public(client):
    """The other direction: a path that stopped being public should fail here,
    not be discovered by a user who can no longer log in.

    Probed by `error_code`, not by status. `/auth/login` with bad credentials
    correctly answers 401 INVALID_CREDENTIALS — a status check alone cannot
    tell that apart from "the middleware demanded a token".
    """
    blocked = []
    for path in sorted(EXPECTED_PUBLIC):
        method = "POST" if "/auth/" in path else "GET"
        response = call(client, method, path)
        if response.status_code == 401 and _error_code(response) == "UNAUTHORIZED":
            blocked.append(path)

    assert blocked == [], f"no longer reachable without a token: {blocked}"


@pytest.mark.parametrize("path", ["/api/v1/admin/documents", "/api/v1/chat/conversations"])
def test_a_malformed_token_is_rejected(client, path):
    for header in ("Bearer", "Bearer ", "Basic abc", "Bearer not.a.jwt", "abc"):
        response = client.get(path, headers={"Authorization": header})
        assert response.status_code == 401, header


def test_an_access_token_cannot_be_used_as_a_refresh_token(client, db, auth):
    token = auth["Authorization"].split(" ", 1)[1]
    response = client.post("/api/v1/auth/refresh", cookies={"erag_refresh": token})
    assert response.status_code == 401


# --- Security headers ---------------------------------------------------- #


def test_security_headers_are_present_on_a_normal_response(client, org_auth):
    response = client.get("/api/v1/admin/documents", headers=org_auth)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_security_headers_are_present_on_a_rejection(client):
    """The headers must not depend on reaching a route."""
    response = client.get("/api/v1/admin/documents")

    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_hsts_is_not_sent_outside_production(client):
    """Pinning a developer's browser to HTTPS for a localhost that serves plain
    HTTP outlives the mistake that caused it."""
    response = client.get("/health")
    assert "Strict-Transport-Security" not in response.headers


def test_cors_is_never_a_wildcard():
    from config.settings import settings

    assert "*" not in settings.CORS_ORIGINS


# --- Rate limiting (§40) ------------------------------------------------- #


def test_login_is_limited_per_ip(client, db):
    """Keyed by IP, not by account: the email field is attacker-controlled, so
    a per-account key would let an attacker reset their own limit."""
    from app.cache.redis_client import ping

    if not ping():
        pytest.skip("Redis is not running")

    from config.settings import settings

    limit = int(settings.RATE_LIMIT_LOGIN.split("/")[0])
    body = {"email": "nobody@example.com", "password": "wrong-password-here"}

    statuses = [
        client.post("/api/v1/auth/login", json=body).status_code
        for _ in range(limit + 2)
    ]

    assert statuses[:limit] == [401] * limit, statuses
    assert statuses[limit] == 429
    assert statuses[-1] == 429


def test_a_rate_limited_response_carries_retry_after(client):
    from app.cache.redis_client import ping

    if not ping():
        pytest.skip("Redis is not running")

    from config.settings import settings

    limit = int(settings.RATE_LIMIT_LOGIN.split("/")[0])
    body = {"email": "nobody@example.com", "password": "wrong-password-here"}
    for _ in range(limit + 1):
        response = client.post("/api/v1/auth/login", json=body)

    assert response.status_code == 429
    assert response.json()["error_code"] == "RATE_LIMIT_EXCEEDED"
    assert int(response.headers["Retry-After"]) >= 1


def test_changing_the_email_does_not_reset_the_login_limit(client):
    from app.cache.redis_client import ping

    if not ping():
        pytest.skip("Redis is not running")

    from config.settings import settings

    limit = int(settings.RATE_LIMIT_LOGIN.split("/")[0])
    for index in range(limit):
        client.post("/api/v1/auth/login",
                    json={"email": f"user{index}@example.com", "password": "wrong-password"})

    response = client.post("/api/v1/auth/login",
                           json={"email": "someone-else@example.com", "password": "wrong"})
    assert response.status_code == 429


def test_remaining_budget_is_advertised(client, org_auth):
    from app.cache.redis_client import ping

    if not ping():
        pytest.skip("Redis is not running")

    first = client.get("/api/v1/admin/documents", headers=org_auth)
    second = client.get("/api/v1/admin/documents", headers=org_auth)

    assert int(first.headers["X-RateLimit-Remaining"]) > int(
        second.headers["X-RateLimit-Remaining"]
    )


def test_health_is_never_rate_limited(client):
    """A probe every second must not consume the operator's own budget."""
    from app.cache.redis_client import ping

    if not ping():
        pytest.skip("Redis is not running")

    for _ in range(50):
        assert client.get("/health").status_code == 200


def test_an_unauthenticated_request_gets_401_not_429(client):
    """RateLimit runs inside Auth, so the caller learns the real problem."""
    from app.cache.redis_client import ping

    if not ping():
        pytest.skip("Redis is not running")

    for _ in range(20):
        response = client.get("/api/v1/admin/documents")
    assert response.status_code == 401


# --- Audit content (§39) ------------------------------------------------- #


def test_audit_rows_never_carry_pii_or_file_contents(client, org_auth, db):
    """`audit_logs.metadata` is JSONB and easy to over-fill."""
    import io as _io

    from app.core.logging import _PII_PATTERNS

    body = b"Aadhaar 2345 6789 0124 and PAN ABCDE1234F should never be echoed.\n"
    client.post(
        "/api/v1/admin/documents/upload",
        files=[("files", ("secrets.txt", _io.BytesIO(body), "text/plain"))],
        headers=org_auth,
    )

    rows = db.execute(text("SELECT metadata::text FROM audit_logs")).scalars().all()
    assert rows, "the upload should have been audited"

    for row in rows:
        assert "2345 6789 0124" not in row
        assert "ABCDE1234F" not in row
        for pattern, label in _PII_PATTERNS:
            assert not pattern.search(row), f"{label} matched audit metadata: {row}"
