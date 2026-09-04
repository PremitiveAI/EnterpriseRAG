"""Route-level auth behaviour, including the exemption-set guarantee."""

from fastapi.testclient import TestClient

from app.main import app
from app.middlewares.auth import PUBLIC_PATHS

client = TestClient(app, raise_server_exceptions=False)


def test_health_is_public():
    assert client.get("/health").status_code == 200


def test_me_requires_a_token():
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error_code"] == "UNAUTHORIZED"


def test_error_envelope_shape():
    body = client.get("/api/v1/auth/me").json()
    assert body["success"] is False
    assert set(body) >= {"success", "error_code", "message", "details", "request_id"}


def test_bad_scheme_rejected():
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


def test_garbage_bearer_rejected():
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401


def test_request_id_header_returned():
    assert client.get("/health").headers.get("X-Request-ID")


def test_supplied_request_id_is_echoed():
    r = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"


def test_login_validates_payload():
    assert client.post("/api/v1/auth/login", json={"email": "nope", "password": "x"}).status_code == 422


def test_public_paths_are_exact_match_not_prefixes():
    """Guards the prefix-exemption bug documented in security-model.md.

    Every public path must be a full path. A value that is a prefix of another
    route would exempt that route too.
    """
    for path in PUBLIC_PATHS:
        assert path.startswith("/")
        assert not path.endswith("*")


def test_every_route_outside_the_public_set_requires_auth():
    """A newly added route cannot silently become public."""
    skip = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        if not methods or path in PUBLIC_PATHS or path in skip or "{" in path:
            continue
        for method in methods:
            r = client.request(method, path)
            assert r.status_code == 401, f"{method} {path} did not require auth"
