"""Super Admin category master
(docs/release-2/features/category-management.md).

The CRUD is the easy part. Two rules are what these tests exist for:

* **A slug cannot change.** The classifier returns a slug, documents store it,
  and Qdrant payloads copy it. Renaming would orphan all three at once with no
  error to notice it by, so the refusal has to be enforced, not documented.
* **A category in use cannot be hard-deleted.** The foreign key is ON DELETE
  SET NULL, so deleting one would silently strip the classification from every
  document that carried it - across every tenant, from one click.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from tests.conftest import make_tenant, otp_login, reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

CATEGORIES = "/api/v1/super-admin/categories"


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


def create_category(client, auth, *, slug="vendor-contracts", name="Vendor Contracts",
                    **extra):
    return client.post(
        CATEGORIES, headers=auth, json={"slug": slug, "name": name, **extra}
    )


def attach_document(db, category_id: str, *, deleted: bool = False) -> None:
    """Give a category one document, without running the upload pipeline.

    The pipeline would spend a minute on OCR and embeddings to produce the one
    thing these tests need: a row whose ``category_id`` points here.
    """
    from datetime import datetime, timezone

    from app.modules.documents.models import Document, DocumentStatus

    organization, admin = make_tenant(db, slug=f"t-{uuid.uuid4().hex[:8]}",
                                      email=f"{uuid.uuid4().hex[:8]}@acme.example")
    db.add(
        Document(
            organization_id=organization.id,
            created_by=admin.id,
            file_name="contract.pdf",
            original_file_name="contract.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            file_size=1024,
            storage_key=f"docs/{uuid.uuid4().hex}.pdf",
            file_hash="a" * 64,
            category_id=uuid.UUID(category_id),
            status=DocumentStatus.COMPLETED,
            deleted_at=datetime.now(timezone.utc) if deleted else None,
        )
    )
    db.commit()


# --- Authorization -------------------------------------------------------- #


def test_writing_categories_requires_a_token(client):
    category_id = uuid.uuid4()
    assert client.get(CATEGORIES).status_code == 401
    assert client.post(CATEGORIES, json={"slug": "x-y", "name": "Xy"}).status_code == 401
    assert client.patch(f"{CATEGORIES}/{category_id}", json={}).status_code == 401
    assert client.delete(f"{CATEGORIES}/{category_id}").status_code == 401


def test_org_admin_can_read_but_not_write_categories(client, auth, db):
    """The taxonomy is global, so every tenant reads it; only the Super Admin
    edits it."""
    created = create_category(client, auth).json()["data"]

    make_tenant(db, email="admin@acme.example")
    org_admin = otp_login(client, "admin@acme.example")

    readable = client.get("/api/v1/admin/documents/categories", headers=org_admin)
    assert readable.status_code == 200
    slugs = [c["slug"] for c in readable.json()["data"]["categories"]]
    assert "vendor-contracts" in slugs

    for call in (
        client.post(CATEGORIES, headers=org_admin, json={"slug": "x-y", "name": "Xy"}),
        client.patch(f"{CATEGORIES}/{created['id']}", headers=org_admin,
                     json={"name": "Renamed"}),
        client.delete(f"{CATEGORIES}/{created['id']}", headers=org_admin),
    ):
        assert call.status_code == 403, call.text
        assert call.json()["error_code"] == "FORBIDDEN"


# --- Create --------------------------------------------------------------- #


def test_creating_a_category_returns_it(client, auth):
    r = create_category(client, auth, description="Supplier agreements.", sort_order=110)

    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["slug"] == "vendor-contracts"
    assert data["is_active"] is True
    assert data["document_count"] == 0
    assert data["sort_order"] == 110


def test_duplicate_slug_rejected(client, auth):
    create_category(client, auth)
    clash = create_category(client, auth, name="Vendor Contracts II")

    assert clash.status_code == 409, clash.text
    assert clash.json()["error_code"] == "CATEGORY_SLUG_TAKEN"


def test_duplicate_name_rejected_rather_than_crashing(client, auth):
    """``name`` is UNIQUE in the schema. Without the check this is an
    IntegrityError and a 500 for someone who merely reused a word."""
    create_category(client, auth)
    clash = create_category(client, auth, slug="vendor-contracts-2")

    assert clash.status_code == 409, clash.text


@pytest.mark.parametrize("slug", ["Vendor Contracts", "vendor_contracts", "-vendor",
                                  "vendor--contracts", "vendor-"])
def test_a_malformed_slug_is_rejected(client, auth, slug):
    r = create_category(client, auth, slug=slug)
    assert r.status_code == 422, f"{slug!r} was accepted"


# --- The immutable slug --------------------------------------------------- #


def test_slug_is_immutable_after_creation(client, auth):
    created = create_category(client, auth).json()["data"]

    r = client.patch(f"{CATEGORIES}/{created['id']}", headers=auth,
                     json={"slug": "supplier-contracts"})

    assert r.status_code == 422, r.text
    assert r.json()["error_code"] == "CATEGORY_SLUG_IMMUTABLE"
    # And nothing changed.
    after = client.get(CATEGORIES, headers=auth).json()["data"]["items"][0]
    assert after["slug"] == "vendor-contracts"


def test_resending_the_same_slug_is_not_a_rename(client, auth):
    """A UI that PATCHes the whole object back would otherwise be unable to
    edit anything at all."""
    created = create_category(client, auth).json()["data"]

    r = client.patch(f"{CATEGORIES}/{created['id']}", headers=auth,
                     json={"slug": "vendor-contracts", "name": "Supplier Contracts"})

    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "Supplier Contracts"


def test_a_rename_attempt_does_not_half_apply(client, auth):
    """The slug is checked before any other field is touched."""
    created = create_category(client, auth).json()["data"]

    client.patch(f"{CATEGORIES}/{created['id']}", headers=auth,
                 json={"slug": "other-slug", "name": "Renamed", "sort_order": 999})

    after = client.get(CATEGORIES, headers=auth).json()["data"]["items"][0]
    assert after["name"] == "Vendor Contracts"
    assert after["sort_order"] == 0


# --- Update --------------------------------------------------------------- #


def test_name_description_and_order_are_editable(client, auth):
    created = create_category(client, auth).json()["data"]

    r = client.patch(
        f"{CATEGORIES}/{created['id']}", headers=auth,
        json={"name": "Supplier Contracts", "description": "SOWs and renewals.",
              "sort_order": 5},
    )

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert (data["name"], data["description"], data["sort_order"]) == (
        "Supplier Contracts", "SOWs and renewals.", 5
    )


def test_unknown_category_is_a_404(client, auth):
    r = client.patch(f"{CATEGORIES}/{uuid.uuid4()}", headers=auth, json={"name": "Nope"})
    assert r.status_code == 404
    assert r.json()["error_code"] == "CATEGORY_NOT_FOUND"


def test_the_list_orders_by_sort_order(client, auth):
    create_category(client, auth, slug="second", name="Second", sort_order=20)
    create_category(client, auth, slug="first", name="First", sort_order=10)

    items = client.get(CATEGORIES, headers=auth).json()["data"]["items"]
    assert [c["slug"] for c in items] == ["first", "second"]


# --- Deactivation --------------------------------------------------------- #


def test_deactivated_category_absent_from_classifier_choices(client, auth, db):
    """The classifier is handed active categories only; offering a retired one
    would keep it being assigned to new documents forever."""
    from app.modules.documents.repositories.document_repository import (
        DocumentRepository,
    )

    created = create_category(client, auth).json()["data"]
    client.patch(f"{CATEGORIES}/{created['id']}", headers=auth, json={"is_active": False})

    choices = [c.slug for c in DocumentRepository(db).active_categories()]
    assert "vendor-contracts" not in choices


def test_deactivated_category_absent_from_the_tenant_filter_bar(client, auth, db):
    created = create_category(client, auth).json()["data"]
    client.patch(f"{CATEGORIES}/{created['id']}", headers=auth, json={"is_active": False})

    make_tenant(db, email="admin@acme.example")
    org_admin = otp_login(client, "admin@acme.example")

    options = client.get("/api/v1/admin/documents/categories", headers=org_admin)
    slugs = [c["slug"] for c in options.json()["data"]["categories"]]
    assert "vendor-contracts" not in slugs


def test_a_deactivated_category_is_still_listed_for_the_super_admin(client, auth):
    """It has to be, or there would be no way to switch it back on."""
    created = create_category(client, auth).json()["data"]
    client.patch(f"{CATEGORIES}/{created['id']}", headers=auth, json={"is_active": False})

    items = client.get(CATEGORIES, headers=auth).json()["data"]["items"]
    assert [c["is_active"] for c in items] == [False]


def test_deactivating_can_be_undone(client, auth):
    created = create_category(client, auth).json()["data"]
    client.patch(f"{CATEGORIES}/{created['id']}", headers=auth, json={"is_active": False})

    r = client.patch(f"{CATEGORIES}/{created['id']}", headers=auth,
                     json={"is_active": True})
    assert r.json()["data"]["is_active"] is True


# --- Delete --------------------------------------------------------------- #


def test_deleting_an_unused_category_removes_it(client, auth):
    created = create_category(client, auth).json()["data"]

    r = client.delete(f"{CATEGORIES}/{created['id']}", headers=auth)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["deleted"] is True
    assert client.get(CATEGORIES, headers=auth).json()["data"]["items"] == []


def test_delete_in_use_category_deactivates_instead(client, auth, db):
    created = create_category(client, auth).json()["data"]
    attach_document(db, created["id"])

    r = client.delete(f"{CATEGORIES}/{created['id']}", headers=auth)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert (data["deleted"], data["deactivated"]) == (False, True)
    assert data["document_count"] == 1


def test_deactivated_category_still_shown_on_existing_documents(client, auth, db):
    """The whole point of deactivating rather than deleting: the document keeps
    the label it was classified with."""
    from app.modules.documents.models import Document

    created = create_category(client, auth).json()["data"]
    attach_document(db, created["id"])

    client.delete(f"{CATEGORIES}/{created['id']}", headers=auth)

    db.expire_all()
    document = db.query(Document).one()
    assert str(document.category_id) == created["id"]
    assert document.category.name == "Vendor Contracts"


def test_a_category_used_only_by_deleted_documents_is_hard_deletable(client, auth, db):
    """Documents in the bin should not pin a category forever."""
    created = create_category(client, auth).json()["data"]
    attach_document(db, created["id"], deleted=True)

    r = client.delete(f"{CATEGORIES}/{created['id']}", headers=auth)
    assert r.json()["data"]["deleted"] is True


def test_document_count_covers_every_organization(client, auth, db):
    """The count is global because the taxonomy is. It is still only a number -
    no title, no owner, nothing an organization owns."""
    created = create_category(client, auth).json()["data"]
    attach_document(db, created["id"])
    attach_document(db, created["id"])

    item = client.get(CATEGORIES, headers=auth).json()["data"]["items"][0]
    assert item["document_count"] == 2


def test_deleting_an_unknown_category_is_a_404(client, auth):
    r = client.delete(f"{CATEGORIES}/{uuid.uuid4()}", headers=auth)
    assert r.status_code == 404


# --- Audit ---------------------------------------------------------------- #


def test_category_actions_are_audited_without_an_organization(client, auth, db):
    """The taxonomy belongs to no tenant. Attributing these rows to one would
    make the audit trail claim a Super Admin acted inside an organization."""
    from sqlalchemy import text

    created = create_category(client, auth).json()["data"]
    client.patch(f"{CATEGORIES}/{created['id']}", headers=auth, json={"name": "Renamed"})
    client.delete(f"{CATEGORIES}/{created['id']}", headers=auth)

    rows = db.execute(
        text("SELECT action, organization_id, actor_type FROM audit_logs "
             "WHERE entity_type = 'document_category' ORDER BY created_at")
    ).all()

    assert [r[0] for r in rows] == ["category.created", "category.updated",
                                    "category.deleted"]
    assert all(r[1] is None for r in rows)
    assert all(r[2] == "SUPER_ADMIN" for r in rows)


def test_deactivation_is_its_own_audit_action(client, auth, db):
    """Distinct from a rename: one retires a label everybody classifies
    against, the other changes how it reads."""
    from sqlalchemy import text

    created = create_category(client, auth).json()["data"]
    client.patch(f"{CATEGORIES}/{created['id']}", headers=auth, json={"is_active": False})

    actions = db.execute(
        text("SELECT action FROM audit_logs WHERE entity_type = 'document_category'")
    ).scalars().all()
    assert "category.deactivated" in actions
