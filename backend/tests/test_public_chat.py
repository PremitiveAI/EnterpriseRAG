"""The public chatbot (docs/release-2/features/public-chatbot.md).

The only unauthenticated path in the system, so the tests that matter are the
ones asserting what it will NOT do: reach a private document, reveal which
organizations exist, or hand a visitor an internal id.
"""

from __future__ import annotations

import io
import secrets
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from tests.conftest import make_tenant, otp_login, reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

ADMIN = "/api/v1/admin/documents"
PUBLIC = "/api/v1/public"

HOURS = b"Opening hours are weekdays 9am to 6pm, closed on public holidays.\n"
SALARIES = b"The chief executive salary is 42 lakh per annum, reviewed each April.\n"


def qdrant_running() -> bool:
    from app.vector.client import is_available

    return is_available()


pytestmark = pytest.mark.skipif(
    not qdrant_running(), reason="Qdrant is not running on QDRANT_URL"
)


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
def org(db):
    organization, admin = make_tenant(db, email="pub@acme.example")
    organization.public_chat_enabled = True
    db.commit()
    return organization, admin


@pytest.fixture()
def auth(client, org):
    return otp_login(client, "pub@acme.example")


def upload_and_index(client, db, headers, name: str, body: bytes) -> str:
    """Upload, then run the pipeline inline so the document is really indexed."""
    from app.modules.documents.services.pipeline_service import ProcessingPipeline
    from app.storage.local import get_storage

    response = client.post(
        f"{ADMIN}/upload",
        files=[("files", (name, io.BytesIO(body), "text/plain"))],
        headers=headers,
    )
    document_id = response.json()["data"]["results"][0]["document_id"]
    result = ProcessingPipeline(db, get_storage()).run(uuid.UUID(document_id))
    assert result.status == "COMPLETED", result.error_code
    return document_id


def publish(client, headers, document_id: str, is_public: bool = True):
    return client.patch(
        f"{ADMIN}/{document_id}/publish", headers=headers, json={"is_public": is_public}
    )


def ask(client, organization_id, message: str, session_id: str | None = None):
    return client.post(
        f"{PUBLIC}/{organization_id}/chat",
        json={"message": message, "session_id": session_id or secrets.token_urlsafe(12)},
    )


# --- The central case ----------------------------------------------------- #


def test_a_published_document_answers_a_visitor(client, db, org, auth):
    organization, _ = org
    document_id = upload_and_index(client, db, auth, "hours.txt", HOURS)
    publish(client, auth, document_id)

    data = ask(client, organization.id, "What are the opening hours?").json()["data"]

    assert data["is_grounded"] is True
    assert [s["document_name"] for s in data["sources"]] == ["hours.txt"]


def test_a_private_document_is_never_reachable(client, db, org, auth):
    """The private document is in the SAME collection and matches the question
    semantically. Only the is_public filter withholds it."""
    organization, _ = org
    upload_and_index(client, db, auth, "salaries.txt", SALARIES)

    data = ask(client, organization.id, "What is the chief executive salary?").json()["data"]

    assert data["is_grounded"] is False
    assert data["sources"] == []
    assert "42" not in data["answer"]
    assert "lakh" not in data["answer"].lower()


def test_unpublishing_withdraws_a_document(client, db, org, auth):
    organization, _ = org
    document_id = upload_and_index(client, db, auth, "hours.txt", HOURS)
    publish(client, auth, document_id)
    assert ask(client, organization.id, "What are the opening hours?").json()["data"][
        "is_grounded"
    ] is True

    publish(client, auth, document_id, is_public=False)

    assert ask(client, organization.id, "What are the opening hours?").json()["data"][
        "is_grounded"
    ] is False


def test_an_organization_with_nothing_published_refuses_everything(client, db, org, auth):
    """Correct, not an error: a refusal is the right answer to an empty corpus."""
    organization, _ = org
    upload_and_index(client, db, auth, "salaries.txt", SALARIES)

    data = ask(client, organization.id, "Tell me anything at all.").json()["data"]
    assert data["is_grounded"] is False


# --- What a visitor may see ---------------------------------------------- #


def test_sources_carry_names_and_pages_but_no_internal_ids(client, db, org, auth):
    """Internal identifiers are useless to a visitor and give an attacker a map."""
    organization, _ = org
    document_id = upload_and_index(client, db, auth, "hours.txt", HOURS)
    publish(client, auth, document_id)

    body = ask(client, organization.id, "When are you open?").text

    assert "document_id" not in body
    assert "chunk_id" not in body
    assert "conversation_id" not in body


def test_config_returns_only_display_information(client, org):
    organization, _ = org
    data = client.get(f"{PUBLIC}/{organization.id}/config").json()["data"]

    assert data["organization_name"] == organization.name
    assert "greeting" in data
    assert "public_chat_key" not in data


# --- Availability is indistinguishable from non-existence ----------------- #


def test_an_unknown_organization_is_404(client):
    assert client.get(f"{PUBLIC}/{uuid.uuid4()}/config").status_code == 404


def test_a_disabled_chatbot_is_404(client, db, org):
    organization, _ = org
    organization.public_chat_enabled = False
    db.commit()

    assert client.get(f"{PUBLIC}/{organization.id}/config").status_code == 404
    assert ask(client, organization.id, "Hello?").status_code == 404


def test_a_suspended_organization_is_404(client, db, org):
    from app.modules.organizations.models import OrganizationStatus

    organization, _ = org
    organization.status = OrganizationStatus.SUSPENDED
    db.commit()

    assert client.get(f"{PUBLIC}/{organization.id}/config").status_code == 404


def test_unknown_disabled_and_suspended_are_indistinguishable(client, db, org):
    """Telling them apart would confirm which organization ids exist."""
    from app.modules.organizations.models import OrganizationStatus

    organization, _ = org
    unknown = client.get(f"{PUBLIC}/{uuid.uuid4()}/config")

    organization.status = OrganizationStatus.SUSPENDED
    db.commit()
    suspended = client.get(f"{PUBLIC}/{organization.id}/config")

    assert unknown.status_code == suspended.status_code == 404
    assert unknown.json()["message"] == suspended.json()["message"]


def test_a_malformed_organization_id_is_not_treated_as_public(client):
    """The public patterns require a UUID. A malformed id does not match them,
    so it falls through to authentication rather than being served - which is
    the safe direction for a path that failed to parse."""
    assert client.get(f"{PUBLIC}/not-a-uuid/config").status_code == 401


# --- Cross-tenant --------------------------------------------------------- #


def test_one_organizations_chatbot_never_answers_from_another(client, db, org, auth):
    organization_a, _ = org
    document_id = upload_and_index(client, db, auth, "hours.txt", HOURS)
    publish(client, auth, document_id)

    organization_b, _ = make_tenant(
        db, name="Globex", slug="globex", email="b@globex.example"
    )
    organization_b.public_chat_enabled = True
    db.commit()

    data = ask(client, organization_b.id, "What are the opening hours?").json()["data"]

    assert data["is_grounded"] is False
    assert data["sources"] == []


# --- Input limits and abuse controls -------------------------------------- #


def test_an_empty_question_is_rejected(client, org):
    organization, _ = org
    r = client.post(
        f"{PUBLIC}/{organization.id}/chat",
        json={"message": "   ", "session_id": secrets.token_urlsafe(12)},
    )
    assert r.status_code == 422


def test_an_overlong_question_is_rejected(client, org):
    organization, _ = org
    r = client.post(
        f"{PUBLIC}/{organization.id}/chat",
        json={"message": "x" * 5000, "session_id": secrets.token_urlsafe(12)},
    )
    assert r.status_code == 422


def test_rate_limiting_applies_to_the_public_endpoint(client, db, org, auth):
    """Bounds the tenant's own spend. Without it, one visitor can run up an
    organization's Gemini bill indefinitely."""
    from app.cache.redis_client import ping

    if not ping():
        pytest.skip("Redis is not running")

    organization, _ = org
    organization.rate_limit_public_chat = "3/minute"
    db.commit()

    statuses = [
        ask(client, organization.id, "hello?").status_code for _ in range(5)
    ]
    assert 429 in statuses


# --- The publish endpoint itself ------------------------------------------ #


def test_publishing_requires_a_processed_document(client, db, org, auth):
    """An unprocessed document has no vectors, so publishing it would advertise
    an empty corpus."""
    response = client.post(
        f"{ADMIN}/upload",
        files=[("files", ("raw.txt", io.BytesIO(HOURS), "text/plain"))],
        headers=auth,
    )
    document_id = response.json()["data"]["results"][0]["document_id"]

    r = publish(client, auth, document_id)
    assert r.status_code == 409
    assert r.json()["error_code"] == "DOCUMENT_NOT_PUBLISHABLE"


def test_documents_are_private_unless_published(client, db, org, auth):
    document_id = upload_and_index(client, db, auth, "hours.txt", HOURS)

    detail = client.get(f"{ADMIN}/{document_id}", headers=auth).json()["data"]
    assert detail["is_public"] is False


def test_publishing_is_audited(client, db, org, auth):
    from sqlalchemy import text

    document_id = upload_and_index(client, db, auth, "hours.txt", HOURS)
    publish(client, auth, document_id)

    actions = db.execute(
        text("SELECT action FROM audit_logs WHERE entity_id = :d"),
        {"d": document_id},
    ).scalars().all()
    assert "document.published" in actions


def test_another_organization_cannot_publish_your_document(client, db, org, auth):
    document_id = upload_and_index(client, db, auth, "hours.txt", HOURS)

    make_tenant(db, name="Globex", slug="globex", email="b@globex.example")
    other = otp_login(client, "b@globex.example")

    assert publish(client, other, document_id).status_code == 404
