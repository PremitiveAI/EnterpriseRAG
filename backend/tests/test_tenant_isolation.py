"""Cross-tenant isolation (docs/release-2/features/tenant-isolation.md).

This is the suite that carries Release 2's central requirement. Everything else
is CRUD over a schema; this is the evidence that an Organization Admin cannot
reach another organization's data.

Two organizations are created for every test, each with its own admin and its
own documents, and every path between them is asserted closed.
"""

from __future__ import annotations

import inspect
import io
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

DOCS = "/api/v1/admin/documents"
CHAT = "/api/v1/chat"


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


def make_org(db, *, name: str, slug: str, admin_email: str):
    """An organization with one admin, created directly."""
    import secrets

    from app.modules.organizations.repositories.admin_repository import (
        OrganizationAdminRepository,
    )
    from app.modules.organizations.repositories.organization_repository import (
        OrganizationRepository,
    )

    org = OrganizationRepository(db).create(
        name=name, slug=slug, public_chat_key=secrets.token_urlsafe(32)
    )
    db.commit()
    admin = OrganizationAdminRepository(db).create(
        organization_id=org.id, full_name=f"{name} Admin", email=admin_email
    )
    db.commit()
    return org, admin


@pytest.fixture()
def org_a(db):
    return make_org(db, name="Acme", slug="acme", admin_email="a@acme.example")


@pytest.fixture()
def org_b(db):
    return make_org(db, name="Globex", slug="globex", admin_email="b@globex.example")


def login(client, email: str) -> dict[str, str]:
    client.post("/api/v1/auth/otp/request", json={"identifier": email})
    r = client.post("/api/v1/auth/otp/verify", json={"identifier": email, "otp": "1111"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def upload(client, headers, *, name="doc.txt", body: bytes | None = None) -> str | None:
    body = body or f"Annual leave is 24 days. {uuid.uuid4()}".encode()
    r = client.post(
        f"{DOCS}/upload",
        files=[("files", (name, io.BytesIO(body), "text/plain"))],
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["results"][0].get("document_id")


@pytest.fixture()
def super_admin_headers(client, db):
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


# --- The list must be filtered in SQL, not afterwards --------------------- #


def test_list_excludes_other_organizations(client, org_a, org_b):
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    upload(client, ha)
    upload(client, ha)

    assert client.get(DOCS, headers=ha).json()["data"]["total"] == 2
    assert client.get(DOCS, headers=hb).json()["data"]["total"] == 0


def test_search_does_not_cross_organizations(client, org_a, org_b):
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    upload(client, ha, name="acme-secret-strategy.txt")

    hits = client.get(DOCS, params={"search": "acme-secret"}, headers=hb).json()["data"]
    assert hits["total"] == 0


def test_filter_options_are_per_organization(client, org_a, org_b):
    """Document types and languages leak the shape of a corpus."""
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    upload(client, ha)

    options = client.get(f"{DOCS}/categories", headers=hb).json()["data"]
    assert options["document_types"] == []
    assert options["languages"] == []


# --- Fetch by id: 404, never 403 ----------------------------------------- #


def test_cross_tenant_fetch_returns_404_not_403(client, org_a, org_b):
    """403 would confirm the id exists, which is itself a disclosure."""
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    document_id = upload(client, ha)

    r = client.get(f"{DOCS}/{document_id}", headers=hb)
    assert r.status_code == 404
    assert r.json()["error_code"] == "DOCUMENT_NOT_FOUND"


def test_owner_can_fetch_the_same_document(client, org_a, org_b):
    """The counterpart: the 404 above is scoping, not a broken lookup."""
    ha = login(client, "a@acme.example")
    document_id = upload(client, ha)

    assert client.get(f"{DOCS}/{document_id}", headers=ha).status_code == 200


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_cross_tenant_mutations_are_refused(client, org_a, org_b, method):
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    document_id = upload(client, ha)

    call = getattr(client, method)
    kwargs = {"json": {"title": "hijacked"}} if method == "patch" else {}
    assert call(f"{DOCS}/{document_id}", headers=hb, **kwargs).status_code == 404


def test_cross_tenant_download_is_refused(client, org_a, org_b):
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    document_id = upload(client, ha)

    assert client.get(f"{DOCS}/{document_id}/download", headers=hb).status_code == 404


def test_cross_tenant_status_is_refused(client, org_a, org_b):
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    document_id = upload(client, ha)

    assert client.get(f"{DOCS}/{document_id}/status", headers=hb).status_code == 404


# --- The token wins over anything in the request -------------------------- #


def test_organization_id_in_the_body_is_ignored(client, org_a, org_b):
    """A caller cannot name an organization. The claim decides."""
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    upload(client, ha)

    org_a_id = str(org_a[0].id)
    listed = client.get(
        DOCS, params={"organization_id": org_a_id}, headers=hb
    ).json()["data"]
    assert listed["total"] == 0


def test_a_forged_organization_claim_is_rejected(client, org_a, org_b):
    """A token whose organization does not match its subject is refused rather
    than silently honoured."""
    import jwt

    from config.settings import settings

    _, admin_b = org_b
    forged = jwt.encode(
        {
            "sub": str(admin_b.id),
            "type": "access",
            "subject_type": "ORG_ADMIN",
            "organization_id": str(org_a[0].id),   # someone else's tenant
            "exp": 9_999_999_999,
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )

    r = client.get(DOCS, headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_a_token_without_an_organization_is_rejected(client, org_a):
    import jwt

    from config.settings import settings

    _, admin = org_a
    token = jwt.encode(
        {
            "sub": str(admin.id),
            "type": "access",
            "subject_type": "ORG_ADMIN",
            # organization_id deliberately absent
            "exp": 9_999_999_999,
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )

    assert client.get(DOCS, headers={"Authorization": f"Bearer {token}"}).status_code == 401


# --- Subject types are disjoint ------------------------------------------ #


@pytest.mark.parametrize("path", [DOCS, f"{CHAT}/conversations"])
def test_super_admin_cannot_reach_tenant_routes(client, super_admin_headers, path):
    """Structural, not a permission check: a Super Admin token carries no
    organization, so there is nothing to scope the query to."""
    r = client.get(path, headers=super_admin_headers)
    assert r.status_code == 403
    assert r.json()["error_code"] == "FORBIDDEN"


def test_org_admin_cannot_reach_super_admin_context(client, org_a):
    """The mirror of the above: an Organization Admin has an organization, and
    Super Admin routes require the absence of one."""
    from app.core.principal import Principal, require_super_admin
    from app.core.exceptions import ForbiddenError

    principal = Principal(
        subject_id=org_a[1].id, subject_type="ORG_ADMIN", organization_id=org_a[0].id
    )
    with pytest.raises(ForbiddenError):
        require_super_admin(principal)


# --- Duplicate detection must not cross the boundary --------------------- #


def test_identical_files_in_two_organizations_are_independent(client, org_a, org_b):
    """Telling Organization A that its file already exists in Organization B
    would itself be a cross-tenant disclosure."""
    ha, hb = login(client, "a@acme.example"), login(client, "b@globex.example")
    body = b"The very same bytes in both organizations.\n"

    a_id = upload(client, ha, body=body)
    b_id = upload(client, hb, body=body)

    assert a_id is not None
    assert b_id is not None
    assert a_id != b_id


def test_duplicate_detection_still_works_inside_one_organization(client, org_a):
    ha = login(client, "a@acme.example")
    body = b"Uploaded twice by the same organization.\n"

    upload(client, ha, body=body)
    r = client.post(
        f"{DOCS}/upload",
        files=[("files", ("again.txt", io.BytesIO(body), "text/plain"))],
        headers=ha,
    )
    assert r.json()["data"]["results"][0]["status"] == "DUPLICATE"


# --- Storage and rows carry the tenant ----------------------------------- #


def test_every_document_row_carries_its_organization(client, db, org_a):
    ha = login(client, "a@acme.example")
    upload(client, ha)

    rows = db.execute(text("SELECT organization_id FROM documents")).scalars().all()
    assert rows
    assert all(str(r) == str(org_a[0].id) for r in rows)


def test_chunks_carry_the_organization_denormalised(db, org_a):
    """Derivable by joining documents, and stored anyway: the failure mode of
    forgetting that join is a silent cross-tenant read."""
    from app.modules.documents.models import DocumentChunk

    column = DocumentChunk.__table__.columns.get("organization_id")
    assert column is not None
    assert column.nullable is False


# --- The guard on the guards --------------------------------------------- #


def test_every_document_repository_read_requires_an_organization():
    """Catches the next method someone adds, not just today's.

    An optional tenant filter fails open, and looks identical to a correct one
    in review. This asserts the parameter exists and has no default.
    """
    from app.modules.documents.repositories.document_repository import DocumentRepository

    exempt = {"__init__", "set_task_id", "replace_tags", "delete_chunks",
              "active_categories", "get_category", "create", "list_documents",
              "_apply_filters"}

    offenders = []
    for name, method in inspect.getmembers(DocumentRepository, inspect.isfunction):
        if name in exempt or name.startswith("__"):
            continue
        params = inspect.signature(method).parameters
        if "organization_id" not in params:
            offenders.append(f"{name}: no organization_id")
        elif params["organization_id"].default is not inspect.Parameter.empty:
            offenders.append(f"{name}: organization_id has a default")

    assert offenders == [], offenders


def test_list_query_requires_an_organization():
    """ListQuery carries the tenant, so building one without it is a TypeError
    rather than a query that returns everything."""
    from app.modules.documents.repositories.document_repository import ListQuery

    with pytest.raises(TypeError):
        ListQuery()  # type: ignore[call-arg]


def test_vector_search_requires_an_organization():
    """The collection is chosen, not filtered - so a missing tenant cannot
    silently widen the search."""
    from app.vector import repository as vector_repo

    params = inspect.signature(vector_repo.search).parameters
    assert "organization_id" in params
    assert params["organization_id"].default is inspect.Parameter.empty
    assert list(params)[0] == "organization_id"


def test_each_organization_gets_its_own_collection():
    from app.vector.client import collection_for

    a, b = uuid.uuid4(), uuid.uuid4()
    assert collection_for(a) != collection_for(b)
    assert collection_for(a).startswith("org_")
    # Deterministic: the same tenant always resolves to the same collection.
    assert collection_for(a) == collection_for(str(a))


# --- Suspension ----------------------------------------------------------- #


def test_a_suspended_organization_cannot_log_in(client, db, org_a):
    from app.modules.organizations.models import OrganizationStatus
    from app.modules.organizations.repositories.organization_repository import (
        OrganizationRepository,
    )

    org, _ = org_a
    OrganizationRepository(db).set_status(org, OrganizationStatus.SUSPENDED)
    db.commit()

    client.post("/api/v1/auth/otp/request", json={"identifier": "a@acme.example"})
    r = client.post(
        "/api/v1/auth/otp/verify", json={"identifier": "a@acme.example", "otp": "1111"}
    )
    assert r.status_code in (401, 403)


def test_suspension_takes_effect_on_the_next_request(client, db, org_a):
    """Re-checked per request, not just at login: suspending must not wait for
    a token to expire."""
    from app.modules.organizations.models import OrganizationStatus
    from app.modules.organizations.repositories.organization_repository import (
        OrganizationRepository,
    )

    headers = login(client, "a@acme.example")
    assert client.get(DOCS, headers=headers).status_code == 200

    org, _ = org_a
    OrganizationRepository(db).set_status(org, OrganizationStatus.SUSPENDED)
    db.commit()

    assert client.get(DOCS, headers=headers).status_code == 403


def test_a_deactivated_admin_loses_access_immediately(client, db, org_a):
    headers = login(client, "a@acme.example")
    assert client.get(DOCS, headers=headers).status_code == 200

    org_a[1].is_active = False
    db.commit()

    assert client.get(DOCS, headers=headers).status_code == 401
