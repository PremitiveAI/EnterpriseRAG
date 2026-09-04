"""Super Admin LLM provider API (ADR-010 §7).

Phase 5. The connection test is patched everywhere: these tests are about the
ordering, the guards and what leaves the process — not about whether Gemini is
reachable from this machine. The one thing never patched is the database, since
every guarantee here is enforced by it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import crypto
from app.modules.ai import llm_config
from app.modules.ai.services import llm_provider_service
from tests.conftest import make_tenant, otp_login

BACKEND_ROOT = Path(__file__).resolve().parent.parent

PROVIDERS = "/api/v1/super-admin/llm-providers"

API_KEY = "AIzaSyD-fake-test-credential-000000000000"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture()
def db():
    """Truncates the same tables as ``reset_database`` and one more, without its
    Qdrant cleanup: nothing in this module indexes a vector, and that helper
    drops collections it judges to be orphans."""
    from app.core.database import SessionLocal
    from tests.conftest import TRUNCATION_ORDER, reset_rate_limits

    session = SessionLocal()
    # Every test in this module signs in, and login is capped at 5/minute per IP.
    reset_rate_limits()
    # Before users: created_by is SET NULL, but the provider rows are this
    # module's own state and must not survive into the next test.
    session.execute(text("DELETE FROM llm_providers"))
    for table in TRUNCATION_ORDER:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    llm_config.reset_cache()
    yield session
    session.rollback()
    session.close()
    llm_config.reset_cache()


@pytest.fixture(autouse=True)
def _provider_answers(monkeypatch):
    """Every connection test succeeds unless a test says otherwise.

    Patched at the service's own seam rather than at the network, so the
    ordering around it - what is committed before and after - is still real.
    """
    monkeypatch.setattr(llm_provider_service.LLMProviderService, "_call",
                        lambda self, config: None)


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def auth(client, db):
    from app.modules.auth.services.auth_service import AuthService

    AuthService(db).create_admin(
        email="root@example.com", password="a-long-test-password", full_name="Root"
    )
    r = client.post("/api/v1/auth/login",
                    json={"email": "root@example.com",
                          "password": "a-long-test-password"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _register(client, auth, *, provider="gemini", model="gemini-2.0-flash",
              key=API_KEY) -> dict:
    r = client.post(PROVIDERS, headers=auth,
                    json={"provider_name": provider, "model_name": model,
                          "api_key": key})
    assert r.status_code == 200, r.text
    return r.json()["data"]


# --- Authorization ---------------------------------------------------------- #


def test_every_route_requires_a_token(client):
    assert client.get(PROVIDERS).status_code == 401
    assert client.post(PROVIDERS, json={}).status_code == 401


def test_an_organization_admin_is_refused_on_every_route(client, db):
    """A tenant switching the provider would be switching it for every other
    tenant, and would be handling a credential that is not theirs."""
    make_tenant(db, email="admin@acme.example")
    org_admin = otp_login(client, "admin@acme.example")
    provider_id = "00000000-0000-0000-0000-000000000000"

    for call in (
        client.get(PROVIDERS, headers=org_admin),
        client.post(PROVIDERS, headers=org_admin, json={}),
        client.patch(f"{PROVIDERS}/{provider_id}", headers=org_admin, json={}),
        client.post(f"{PROVIDERS}/{provider_id}/test", headers=org_admin),
        client.post(f"{PROVIDERS}/{provider_id}/activate", headers=org_admin),
        client.request("DELETE", f"{PROVIDERS}/{provider_id}", headers=org_admin),
    ):
        assert call.status_code == 403, call.text
        assert call.json()["error_code"] == "FORBIDDEN"


# --- The credential never comes back ---------------------------------------- #


def test_no_route_ever_returns_the_credential(client, auth, db):
    """The strongest form of this guarantee is structural: no response model
    has a field to put a key in. Asserted anyway, on the wire."""
    created = _register(client, auth)

    bodies = [
        client.get(PROVIDERS, headers=auth).text,
        client.get(f"{PROVIDERS}/{created['id']}", headers=auth).text,
        client.post(f"{PROVIDERS}/{created['id']}/test", headers=auth).text,
        client.post(f"{PROVIDERS}/{created['id']}/activate", headers=auth).text,
    ]

    for body in bodies:
        assert API_KEY not in body
        assert "encrypted_api_key" not in body
        assert "api_key" not in body


def test_the_fingerprint_stands_in_for_the_key(client, auth, db):
    created = _register(client, auth)

    assert created["key_fingerprint"] == crypto.fingerprint(API_KEY)
    assert created["encryption_key_id"] == crypto.active_key_id()


def test_the_stored_credential_is_encrypted(client, auth, db):
    _register(client, auth)

    stored = db.execute(text("SELECT encrypted_api_key FROM llm_providers")).scalar_one()

    assert API_KEY not in stored
    assert crypto.decrypt(stored) == API_KEY


# --- Registering ------------------------------------------------------------ #


def test_a_registered_provider_is_not_yet_answering(client, auth, db):
    """"Try a provider" and "point all traffic at it" must not be one gesture."""
    created = _register(client, auth)

    assert created["is_active"] is False
    with pytest.raises(Exception):
        llm_config.get_config(db)


def test_a_failing_credential_is_never_stored(client, auth, db, monkeypatch):
    """Nothing is written. A Super Admin retries against the row they had, not
    against a broken one this call left behind."""
    from app.core.exceptions import LLMProviderTestFailedError

    def _refuse(self, config):
        raise LLMProviderTestFailedError()

    monkeypatch.setattr(llm_provider_service.LLMProviderService, "_call", _refuse)

    r = client.post(PROVIDERS, headers=auth,
                    json={"provider_name": "gemini", "model_name": "gemini-2.0-flash",
                          "api_key": API_KEY})

    assert r.status_code == 422, r.text
    assert r.json()["error_code"] == "LLM_PROVIDER_TEST_FAILED"
    assert db.execute(text("SELECT count(*) FROM llm_providers")).scalar_one() == 0


def test_a_prefixed_model_id_is_stored_bare(client, auth, db):
    """`openai/gpt-4o` and `gpt-4o` must not become two different rows, and the
    prefix is composed once at call time - a stored one would be doubled."""
    created = _register(client, auth, model="gemini/gemini-2.0-flash")

    assert created["model_name"] == "gemini-2.0-flash"


def test_an_empty_or_whitespace_key_is_rejected(client, auth, db):
    r = client.post(PROVIDERS, headers=auth,
                    json={"provider_name": "gemini", "model_name": "gemini-2.0-flash",
                          "api_key": "        "})

    assert r.status_code == 422, r.text


def test_an_unknown_provider_is_rejected(client, auth, db):
    r = client.post(PROVIDERS, headers=auth,
                    json={"provider_name": "mistral", "model_name": "mistral-large",
                          "api_key": API_KEY})

    assert r.status_code == 422, r.text


# --- Activating ------------------------------------------------------------- #


def test_activation_switches_the_resolved_configuration(client, auth, db):
    """The end-to-end claim: no restart, no invalidation call."""
    first = _register(client, auth, model="gemini-2.0-flash")
    client.post(f"{PROVIDERS}/{first['id']}/activate", headers=auth)
    assert llm_config.get_config(db).model == "gemini-2.0-flash"

    second = _register(client, auth, model="gemini-2.5-pro")
    r = client.post(f"{PROVIDERS}/{second['id']}/activate", headers=auth)

    assert r.status_code == 200, r.text
    assert llm_config.get_config(db).model == "gemini-2.5-pro"


def test_activating_deactivates_the_previous_one(client, auth, db):
    first = _register(client, auth, model="gemini-2.0-flash")
    second = _register(client, auth, model="gemini-2.5-pro")

    client.post(f"{PROVIDERS}/{first['id']}/activate", headers=auth)
    client.post(f"{PROVIDERS}/{second['id']}/activate", headers=auth)

    active = db.execute(
        text("SELECT model_name FROM llm_providers WHERE is_active")
    ).scalars().all()
    assert active == ["gemini-2.5-pro"]


def test_activation_bumps_the_version(client, auth, db):
    """What makes a second process notice."""
    first = _register(client, auth, model="gemini-2.0-flash")
    second = _register(client, auth, model="gemini-2.5-pro")

    before = client.post(f"{PROVIDERS}/{first['id']}/activate",
                         headers=auth).json()["data"]["config_version"]
    after = client.post(f"{PROVIDERS}/{second['id']}/activate",
                        headers=auth).json()["data"]["config_version"]

    assert after > before


def test_a_failing_provider_is_never_activated(client, auth, db, monkeypatch):
    """The pre-flight is the point of the ordering: a provider that cannot
    answer must not become the one every tenant depends on."""
    from app.core.exceptions import LLMProviderTestFailedError

    working = _register(client, auth, model="gemini-2.0-flash")
    client.post(f"{PROVIDERS}/{working['id']}/activate", headers=auth)
    broken = _register(client, auth, model="gemini-2.5-pro")

    monkeypatch.setattr(
        llm_provider_service.LLMProviderService, "_call",
        lambda self, config: (_ for _ in ()).throw(LLMProviderTestFailedError()),
    )
    r = client.post(f"{PROVIDERS}/{broken['id']}/activate", headers=auth)

    assert r.status_code == 422, r.text
    # The working provider is untouched.
    assert llm_config.get_config(db).model == "gemini-2.0-flash"


def test_a_credential_changed_during_verification_is_a_409(client, auth, db, monkeypatch):
    """The window the fingerprint check closes.

    The test runs outside the transaction - deliberately, so a third-party HTTP
    call never holds row locks - which means another Super Admin can edit the
    row while it runs. Activating anyway would put live a configuration that
    nothing verified.
    """
    target = _register(client, auth)
    replacement = crypto.encrypt("someone-elses-newer-credential")

    def _edit_during_the_call(self, config):
        """Stands in for a concurrent PATCH landing mid-verification."""
        from app.core.database import SessionLocal

        other = SessionLocal()
        other.execute(
            text("UPDATE llm_providers SET encrypted_api_key = :t, key_fingerprint = :f "
                 "WHERE id = :id"),
            {"t": replacement.token, "f": replacement.fingerprint, "id": target["id"]},
        )
        other.commit()
        other.close()

    monkeypatch.setattr(llm_provider_service.LLMProviderService, "_call",
                        _edit_during_the_call)

    r = client.post(f"{PROVIDERS}/{target['id']}/activate", headers=auth)

    assert r.status_code == 409, r.text
    assert r.json()["error_code"] == "LLM_PROVIDER_CHANGED"
    assert db.execute(
        text("SELECT count(*) FROM llm_providers WHERE is_active")
    ).scalar_one() == 0


def test_activating_an_unknown_provider_is_404(client, auth, db):
    r = client.post(f"{PROVIDERS}/00000000-0000-0000-0000-000000000000/activate",
                    headers=auth)

    assert r.status_code == 404, r.text
    assert r.json()["error_code"] == "LLM_PROVIDER_NOT_FOUND"


# --- Updating and deleting -------------------------------------------------- #


def test_omitting_the_key_keeps_the_stored_one(client, auth, db):
    """A blank field in a form must not be how a working credential is erased."""
    created = _register(client, auth)

    r = client.patch(f"{PROVIDERS}/{created['id']}", headers=auth,
                     json={"model_name": "gemini-2.5-pro"})

    assert r.status_code == 200, r.text
    assert r.json()["data"]["key_fingerprint"] == crypto.fingerprint(API_KEY)
    stored = db.execute(text("SELECT encrypted_api_key FROM llm_providers")).scalar_one()
    assert crypto.decrypt(stored) == API_KEY


def test_replacing_the_key_re_encrypts_and_re_fingerprints(client, auth, db):
    created = _register(client, auth)

    r = client.patch(f"{PROVIDERS}/{created['id']}", headers=auth,
                     json={"api_key": "a-brand-new-credential-value"})

    assert r.json()["data"]["key_fingerprint"] == crypto.fingerprint(
        "a-brand-new-credential-value"
    )


def test_the_active_provider_cannot_be_deleted(client, auth, db):
    created = _register(client, auth)
    client.post(f"{PROVIDERS}/{created['id']}/activate", headers=auth)

    r = client.request("DELETE", f"{PROVIDERS}/{created['id']}", headers=auth)

    assert r.status_code == 409, r.text
    assert r.json()["error_code"] == "LLM_PROVIDER_ACTIVE"
    assert db.execute(text("SELECT count(*) FROM llm_providers")).scalar_one() == 1


def test_an_inactive_provider_can_be_deleted(client, auth, db):
    created = _register(client, auth)

    r = client.request("DELETE", f"{PROVIDERS}/{created['id']}", headers=auth)

    assert r.status_code == 200, r.text
    assert db.execute(text("SELECT count(*) FROM llm_providers")).scalar_one() == 0


# --- Audit ------------------------------------------------------------------ #


def test_every_activation_is_audited_without_the_key(client, auth, db):
    """The compensating control for the whole feature. A Super Admin can
    repoint every tenant's answering model, and authorization cannot restrict
    that - so the guarantee is that it cannot be done silently."""
    created = _register(client, auth)
    client.post(f"{PROVIDERS}/{created['id']}/activate", headers=auth)

    rows = db.execute(
        text("SELECT action, actor_type, organization_id, metadata::text "
             "FROM audit_logs WHERE entity_type = 'llm_provider' ORDER BY created_at")
    ).all()

    assert [r[0] for r in rows] == ["llm_provider.created", "llm_provider.activated"]
    assert all(r[1] == "SUPER_ADMIN" for r in rows)
    # Global, not a tenant action.
    assert all(r[2] is None for r in rows)
    assert all(API_KEY not in r[3] for r in rows)
    assert crypto.fingerprint(API_KEY) in rows[1][3]


def test_a_deletion_is_audited(client, auth, db):
    created = _register(client, auth)
    client.request("DELETE", f"{PROVIDERS}/{created['id']}", headers=auth)

    actions = db.execute(
        text("SELECT action FROM audit_logs WHERE entity_type = 'llm_provider' "
             "ORDER BY created_at")
    ).scalars().all()

    assert actions == ["llm_provider.created", "llm_provider.deleted"]
