"""Super Admin organization management
(docs/release-2/features/organization-management.md).

Two properties matter more than the CRUD itself:

* **The Super Admin cannot read organization data.** Every endpoint here is
  asserted to return counts and configuration only.
* **Contact changes are visible.** A Super Admin who repoints an admin's email
  can receive that admin's OTP. Access control cannot prevent it, so the
  guarantee is "cannot read SILENTLY" - and the audit row is what makes that
  true.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import make_tenant, otp_login, reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

SA = "/api/v1/super-admin"
ORGS = f"{SA}/organizations"


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
    yield


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def auth(client, db):
    """A signed-in Super Admin."""
    from app.modules.auth.services.auth_service import AuthService

    AuthService(db).create_admin(
        email="root@example.com", password="a-long-test-password", full_name="Root"
    )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "root@example.com", "password": "a-long-test-password"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def create_org(client, auth, *, name="Acme", slug="acme", **extra):
    return client.post(
        ORGS, headers=auth,
        json={"name": name, "slug": slug, **extra},
    )


# --- Authorization -------------------------------------------------------- #


def test_every_super_admin_route_requires_a_token(client):
    org_id = uuid.uuid4()
    assert client.get(ORGS).status_code == 401
    assert client.post(ORGS, json={"name": "x", "slug": "x"}).status_code == 401
    assert client.get(f"{ORGS}/{org_id}").status_code == 401
    assert client.delete(f"{ORGS}/{org_id}").status_code == 401


def test_an_organization_admin_cannot_reach_these_routes(client, db):
    """The mirror of a Super Admin being refused tenant routes."""
    make_tenant(db, email="admin@acme.example")
    org_admin = otp_login(client, "admin@acme.example")

    r = client.get(ORGS, headers=org_admin)
    assert r.status_code == 403
    assert r.json()["error_code"] == "FORBIDDEN"


# --- Create --------------------------------------------------------------- #


def test_creating_an_organization_returns_it(client, auth):
    r = create_org(client, auth, contact_email="ops@acme.example")

    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["name"] == "Acme"
    assert data["status"] == "ACTIVE"
    assert data["counts"]["documents"] == 0
    # Generated server-side, never supplied by the caller.
    assert len(data["public_chat_key"]) > 20


def test_creating_an_organization_provisions_its_collection(client, auth):
    """An organization with no collection would accept uploads that then fail
    at the indexing stage with no obvious cause."""
    from app.vector.client import collection_for, get_client

    if not get_client:
        pytest.skip("Qdrant unavailable")

    data = create_org(client, auth).json()["data"]
    names = [c.name for c in get_client().get_collections().collections]

    assert collection_for(data["id"]) in names


def test_a_duplicate_slug_is_rejected(client, auth):
    create_org(client, auth)
    r = create_org(client, auth, name="Acme Two")

    assert r.status_code == 409
    assert r.json()["error_code"] == "CONFLICT"


@pytest.mark.parametrize("slug", ["acme corp", "-acme", "acme-", "acme_corp", "a c"])
def test_a_malformed_slug_is_rejected(client, auth, slug):
    """The slug names the Qdrant collection, so its shape is not cosmetic."""
    assert create_org(client, auth, slug=slug).status_code == 422


def test_slug_case_is_normalised_rather_than_rejected(client, auth):
    """Uppercase is a typo, not an error - lowercasing it is friendlier than a
    422, and the stored value is still canonical."""
    data = create_org(client, auth, slug="Acme").json()["data"]
    assert data["slug"] == "acme"


def test_public_chat_is_off_by_default(client, auth):
    """Exposing an organization publicly is never a side effect of creating it."""
    data = create_org(client, auth).json()["data"]
    assert data["public_chat_enabled"] is False


# --- The privacy guarantee ------------------------------------------------ #


def test_detail_returns_counts_never_content(client, auth, db):
    """Browsing an organization's document titles would already be reading its
    data. This is the mechanism behind the guarantee, not a UI choice."""
    org_id = create_org(client, auth).json()["data"]["id"]

    from app.modules.organizations.repositories.admin_repository import (
        OrganizationAdminRepository,
    )

    OrganizationAdminRepository(db).create(
        organization_id=uuid.UUID(org_id), full_name="Asha Rao", email="a@acme.example"
    )
    db.commit()
    headers = otp_login(client, "a@acme.example")
    client.post(
        "/api/v1/admin/documents/upload",
        files=[("files", ("secret-strategy.txt", io.BytesIO(b"confidential\n"), "text/plain"))],
        headers=headers,
    )

    body = client.get(f"{ORGS}/{org_id}", headers=auth).text

    assert "secret-strategy" not in body
    assert "confidential" not in body
    assert '"documents":1' in body.replace(" ", "")


def test_the_list_never_carries_document_or_chat_content(client, auth, db):
    org_id = create_org(client, auth).json()["data"]["id"]
    body = client.get(ORGS, headers=auth).text

    assert "documents" in body          # the count
    assert "file_name" not in body      # not the content
    assert "title" not in body


# --- List ----------------------------------------------------------------- #


def test_list_paginates_and_totals(client, auth):
    for i in range(5):
        create_org(client, auth, name=f"Org {i}", slug=f"org-{i}")

    data = client.get(ORGS, params={"page": 1, "page_size": 2}, headers=auth).json()["data"]
    assert len(data["items"]) == 2
    assert data["total"] == 5
    assert data["total_pages"] == 3


def test_list_search_matches_name_and_slug(client, auth):
    create_org(client, auth, name="Acme Corporation", slug="acme")
    create_org(client, auth, name="Globex", slug="globex")

    data = client.get(ORGS, params={"search": "glob"}, headers=auth).json()["data"]
    assert [i["slug"] for i in data["items"]] == ["globex"]


def test_an_unknown_status_filter_is_rejected(client, auth):
    assert client.get(ORGS, params={"status": "NONSENSE"}, headers=auth).status_code == 422


# --- Update --------------------------------------------------------------- #


def test_updating_limits_persists(client, auth):
    org_id = create_org(client, auth).json()["data"]["id"]

    r = client.patch(f"{ORGS}/{org_id}", headers=auth,
                     json={"max_documents": 500, "rate_limit_chat": "60/minute"})

    assert r.status_code == 200
    limits = r.json()["data"]["limits"]
    assert limits["max_documents"] == 500
    assert limits["rate_limit_chat"] == "60/minute"


def test_a_malformed_rate_limit_is_rejected_at_save_time(client, auth):
    """A mistyped limit must not silently become no limit at all - the same
    reasoning as parse_rule rejecting a bare count."""
    org_id = create_org(client, auth).json()["data"]["id"]

    r = client.patch(f"{ORGS}/{org_id}", headers=auth, json={"rate_limit_chat": "60"})
    assert r.status_code == 422


def test_a_null_limit_means_inherit_the_default(client, auth):
    org_id = create_org(client, auth, max_documents=10).json()["data"]["id"]

    r = client.patch(f"{ORGS}/{org_id}", headers=auth, json={"max_documents": None})
    assert r.json()["data"]["limits"]["max_documents"] is None


# --- Suspend, activate, delete -------------------------------------------- #


def test_suspending_blocks_admin_login(client, auth, db):
    org_id = create_org(client, auth).json()["data"]["id"]
    client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                json={"full_name": "Asha Rao", "email": "a@acme.example"})

    client.post(f"{ORGS}/{org_id}/suspend", headers=auth)

    client.post("/api/v1/auth/otp/request", json={"identifier": "a@acme.example"})
    r = client.post("/api/v1/auth/otp/verify",
                    json={"identifier": "a@acme.example", "otp": "1111"})
    assert r.status_code in (401, 403)


def test_activating_restores_access_with_data_intact(client, auth, db):
    org_id = create_org(client, auth).json()["data"]["id"]
    client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                json={"full_name": "Asha Rao", "email": "a@acme.example"})
    client.post(f"{ORGS}/{org_id}/suspend", headers=auth)

    r = client.post(f"{ORGS}/{org_id}/activate", headers=auth)
    assert r.json()["data"]["status"] == "ACTIVE"

    assert otp_login(client, "a@acme.example")  # works again


def test_deleting_an_empty_organization_succeeds(client, auth):
    org_id = create_org(client, auth).json()["data"]["id"]

    r = client.delete(f"{ORGS}/{org_id}", headers=auth)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "DELETED"


def test_deleting_an_organization_with_documents_requires_force(client, auth, db):
    """Deleting a tenant is unrecoverable in a way suspension is not."""
    org_id = create_org(client, auth).json()["data"]["id"]
    client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                json={"full_name": "Asha Rao", "email": "a@acme.example"})
    headers = otp_login(client, "a@acme.example")
    client.post(
        "/api/v1/admin/documents/upload",
        files=[("files", ("doc.txt", io.BytesIO(b"content\n"), "text/plain"))],
        headers=headers,
    )

    refused = client.delete(f"{ORGS}/{org_id}", headers=auth)
    assert refused.status_code == 409

    forced = client.delete(f"{ORGS}/{org_id}", params={"force": True}, headers=auth)
    assert forced.status_code == 200


def test_deleting_drops_the_collection(client, auth):
    from app.vector.client import collection_for, get_client

    org_id = create_org(client, auth).json()["data"]["id"]
    client.delete(f"{ORGS}/{org_id}", headers=auth)

    names = [c.name for c in get_client().get_collections().collections]
    assert collection_for(org_id) not in names


def test_a_deleted_organization_is_gone_from_the_list(client, auth):
    org_id = create_org(client, auth).json()["data"]["id"]
    client.delete(f"{ORGS}/{org_id}", headers=auth)

    assert client.get(ORGS, headers=auth).json()["data"]["total"] == 0


# --- Organization admins -------------------------------------------------- #


def test_creating_an_admin(client, auth):
    org_id = create_org(client, auth).json()["data"]["id"]

    r = client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                    json={"full_name": "Priya Sharma", "email": "priya@acme.example",
                          "mobile": "+919876543210"})

    assert r.status_code == 201
    data = r.json()["data"]
    assert data["email"] == "priya@acme.example"
    assert data["is_active"] is True
    # There is no password to return, because there is none to set.
    assert "password" not in r.text


def test_an_admin_can_then_log_in(client, auth):
    org_id = create_org(client, auth).json()["data"]["id"]
    client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                json={"full_name": "Asha Rao", "email": "a@acme.example"})

    assert otp_login(client, "a@acme.example")


def test_identifiers_are_unique_across_organizations(client, auth):
    """An OTP identifier must resolve to exactly one admin, or login is
    ambiguous across tenants."""
    a = create_org(client, auth, name="Acme", slug="acme").json()["data"]["id"]
    b = create_org(client, auth, name="Globex", slug="globex").json()["data"]["id"]

    client.post(f"{ORGS}/{a}/admins", headers=auth,
                json={"full_name": "Asha Rao", "email": "same@example.com"})
    clash = client.post(f"{ORGS}/{b}/admins", headers=auth,
                        json={"full_name": "Bala Iyer", "email": "same@example.com"})

    assert clash.status_code == 409


def test_changing_an_email_writes_its_own_audit_action(client, auth, db):
    """🔴 The residual hole in the privacy guarantee, made visible.

    A Super Admin who repoints an admin's email receives that admin's OTP.
    Access control cannot prevent it - managing admins is a legitimate power -
    so this row is what stops it being quiet.
    """
    org_id = create_org(client, auth).json()["data"]["id"]
    admin_id = client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                           json={"full_name": "Asha Rao", "email": "a@acme.example"}
                           ).json()["data"]["id"]

    client.patch(f"{SA}/admins/{admin_id}", headers=auth,
                 json={"email": "attacker@evil.example"})

    rows = db.execute(
        text("SELECT action, metadata::text FROM audit_logs WHERE entity_id = :a"),
        {"a": admin_id},
    ).all()
    actions = [r[0] for r in rows]

    assert "organization_admin.contact_changed" in actions
    # The old and new values are both recorded, so the change is reviewable.
    changed = next(r[1] for r in rows if r[0] == "organization_admin.contact_changed")
    assert "a@acme.example" in changed
    assert "attacker@evil.example" in changed


def test_renaming_an_admin_is_not_a_contact_change(client, auth, db):
    """Only email and mobile carry the OTP risk; a name does not."""
    org_id = create_org(client, auth).json()["data"]["id"]
    admin_id = client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                           json={"full_name": "Asha Rao", "email": "a@acme.example"}
                           ).json()["data"]["id"]

    client.patch(f"{SA}/admins/{admin_id}", headers=auth, json={"full_name": "Renamed"})

    actions = db.execute(
        text("SELECT action FROM audit_logs WHERE entity_id = :a"), {"a": admin_id}
    ).scalars().all()
    assert "organization_admin.contact_changed" not in actions


def test_deactivating_an_admin_revokes_access_immediately(client, auth):
    org_id = create_org(client, auth).json()["data"]["id"]
    admin_id = client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                           json={"full_name": "Asha Rao", "email": "a@acme.example"}
                           ).json()["data"]["id"]
    headers = otp_login(client, "a@acme.example")
    assert client.get("/api/v1/admin/documents", headers=headers).status_code == 200

    client.delete(f"{SA}/admins/{admin_id}", headers=auth)

    # Re-checked per request, not trusted from the claim.
    assert client.get("/api/v1/admin/documents", headers=headers).status_code == 401


def test_admin_endpoints_never_expose_organization_content(client, auth):
    org_id = create_org(client, auth).json()["data"]["id"]
    client.post(f"{ORGS}/{org_id}/admins", headers=auth,
                json={"full_name": "Asha Rao", "email": "a@acme.example"})

    body = client.get(f"{ORGS}/{org_id}/admins", headers=auth).text
    assert "document" not in body.lower()
    assert "conversation" not in body.lower()


# --- Auditing ------------------------------------------------------------- #


def test_every_mutation_is_audited_as_a_super_admin_action(client, auth, db):
    org_id = create_org(client, auth).json()["data"]["id"]
    client.patch(f"{ORGS}/{org_id}", headers=auth, json={"name": "Renamed"})
    client.post(f"{ORGS}/{org_id}/suspend", headers=auth)

    rows = db.execute(
        text("""SELECT action, actor_type, super_admin_id, organization_admin_id
                FROM audit_logs WHERE organization_id = :o"""),
        {"o": org_id},
    ).all()

    actions = {r[0] for r in rows}
    assert {"organization.created", "organization.updated",
            "organization.suspended"} <= actions

    for row in rows:
        assert row[1] == "SUPER_ADMIN"
        # Routed to the right column: an untyped id could not be foreign-keyed.
        assert row[2] is not None
        assert row[3] is None
