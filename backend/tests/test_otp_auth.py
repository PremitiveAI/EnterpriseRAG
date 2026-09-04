"""OTP authentication (docs/release-2/features/otp-authentication.md).

A 4-digit code is 10,000 combinations. Expiry, single use and the attempt cap
are what make it authentication rather than a formality, so they are the tests
that matter here — alongside the guard that makes the static code impossible in
production.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

REQUEST = "/api/v1/auth/otp/request"
VERIFY = "/api/v1/auth/otp/verify"


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
def admin(db):
    from app.modules.organizations.repositories.admin_repository import (
        OrganizationAdminRepository,
    )
    from app.modules.organizations.repositories.organization_repository import (
        OrganizationRepository,
    )

    org = OrganizationRepository(db).create(
        name="Acme", slug="acme", public_chat_key=secrets.token_urlsafe(32)
    )
    db.commit()
    record = OrganizationAdminRepository(db).create(
        organization_id=org.id,
        full_name="Priya Sharma",
        email="priya@acme.example",
        mobile="+91 98765 43210",
    )
    db.commit()
    return record


# --- The happy path ------------------------------------------------------ #


def test_login_with_email(client, admin):
    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    r = client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"})

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["organization"]["name"] == "Acme"
    assert data["access_token"]


def test_login_with_mobile_ignores_formatting(client, admin):
    """Stored normalised, so "+91 98765 43210" and "+919876543210" are one
    identifier. A mismatch here reads as "the OTP does not work"."""
    client.post(REQUEST, json={"identifier": "+919876543210"})
    r = client.post(VERIFY, json={"identifier": "+91 98765 43210", "otp": "1111"})

    assert r.status_code == 200


def test_the_token_carries_the_subject_type_and_organization(client, admin):
    import jwt

    from config.settings import settings

    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    token = client.post(
        VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"}
    ).json()["data"]["access_token"]

    claims = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    assert claims["subject_type"] == "ORG_ADMIN"
    assert claims["organization_id"] == str(admin.organization_id)


def test_verification_records_the_login(client, db, admin):
    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"})

    db.refresh(admin)
    assert admin.last_login_at is not None


# --- No enumeration ------------------------------------------------------ #


def test_unknown_identifier_is_indistinguishable(client, admin):
    known = client.post(REQUEST, json={"identifier": "priya@acme.example"})
    unknown = client.post(REQUEST, json={"identifier": "nobody@nowhere.example"})

    assert known.status_code == unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]


def test_a_row_is_written_even_for_an_unknown_identifier(client, db, admin):
    """Short-circuiting would make an unknown identifier measurably faster to
    reject - an enumeration oracle - and this also records the probe."""
    client.post(REQUEST, json={"identifier": "nobody@nowhere.example"})

    row = db.execute(
        text("SELECT organization_admin_id FROM otp_requests WHERE identifier = :i"),
        {"i": "nobody@nowhere.example"},
    ).first()
    assert row is not None
    assert row[0] is None


def test_a_code_issued_for_an_unknown_identifier_cannot_log_in(client, admin):
    client.post(REQUEST, json={"identifier": "nobody@nowhere.example"})
    r = client.post(VERIFY, json={"identifier": "nobody@nowhere.example", "otp": "1111"})

    assert r.status_code == 401


# --- The controls that make 4 digits sufficient -------------------------- #


def test_wrong_code_is_rejected(client, admin):
    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    r = client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "9999"})

    assert r.status_code == 401


def test_code_burns_after_five_attempts(client, admin):
    """Without this, 10,000 combinations is a few seconds of scripting."""
    client.post(REQUEST, json={"identifier": "priya@acme.example"})

    for _ in range(5):
        client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "0000"})

    # Even the CORRECT code no longer works.
    r = client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"})
    assert r.status_code == 401


def test_code_is_single_use(client, admin):
    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    assert client.post(
        VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"}
    ).status_code == 200

    again = client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"})
    assert again.status_code == 401


def test_expired_code_is_rejected(client, db, admin):
    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    db.execute(
        text("UPDATE otp_requests SET expires_at = :t"),
        {"t": datetime.now(timezone.utc) - timedelta(minutes=1)},
    )
    db.commit()

    r = client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"})
    assert r.status_code == 401


def test_verifying_without_requesting_fails(client, admin):
    r = client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"})
    assert r.status_code == 401


def test_the_code_is_stored_hashed(client, db, admin):
    """A database read must not yield a working code."""
    client.post(REQUEST, json={"identifier": "priya@acme.example"})

    stored = db.execute(text("SELECT otp_hash FROM otp_requests")).scalar_one()
    assert stored != "1111"
    assert len(stored) > 20


def test_requests_are_rate_limited_per_identifier(client, admin):
    """Stops one inbox being flooded, which an attempt cap alone does not
    cover."""
    statuses = [
        client.post(REQUEST, json={"identifier": "priya@acme.example"}).status_code
        for _ in range(7)
    ]
    assert 429 in statuses


def test_the_newest_code_supersedes_the_previous_one(client, admin):
    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    client.post(REQUEST, json={"identifier": "priya@acme.example"})

    # Both are "1111" outside production, so this asserts the newest row is the
    # one consulted rather than the oldest.
    assert client.post(
        VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"}
    ).status_code == 200


# --- Account and organization state -------------------------------------- #


def test_a_deactivated_admin_cannot_verify(client, db, admin):
    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    admin.is_active = False
    db.commit()

    r = client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"})
    assert r.status_code == 401
    # The same code a wrong OTP gives: the login surface reveals no account
    # state, so "disabled" and "wrong code" are indistinguishable.
    assert r.json()["error_code"] == "OTP_INVALID"


def test_a_suspended_organization_blocks_login(client, db, admin):
    from app.modules.organizations.models import OrganizationStatus

    client.post(REQUEST, json={"identifier": "priya@acme.example"})
    admin.organization.status = OrganizationStatus.SUSPENDED
    db.commit()

    r = client.post(VERIFY, json={"identifier": "priya@acme.example", "otp": "1111"})
    assert r.status_code in (401, 403)


# --- The environment guard ------------------------------------------------ #


def test_the_static_code_is_impossible_in_production(monkeypatch):
    """A misconfigured environment flag would otherwise mean anyone who knows an
    email address can log in as that administrator."""
    from app.modules.organizations.services import otp_service

    monkeypatch.setattr(
        type(otp_service.settings), "is_production", property(lambda self: True)
    )
    code = otp_service.generate_code()

    assert code != otp_service.STATIC_OTP
    assert len(code) == otp_service.OTP_LENGTH
    assert code.isdigit()


def test_an_unexpected_environment_refuses_to_issue_a_static_code(monkeypatch):
    from app.modules.organizations.services import otp_service

    monkeypatch.setattr(otp_service.settings, "ENVIRONMENT", "preprod")
    with pytest.raises(RuntimeError, match="Refusing to issue a static OTP"):
        otp_service.generate_code()


def test_uat_maps_onto_staging(monkeypatch):
    """"UAT" is the existing `staging` value; no new environment was added."""
    from app.modules.organizations.services import otp_service

    monkeypatch.setattr(otp_service.settings, "ENVIRONMENT", "staging")
    assert otp_service.generate_code() == otp_service.STATIC_OTP


def test_organization_admins_have_no_password_column():
    """The structural half of the Super Admin privacy guarantee: there is no
    credential for a Super Admin to set or reset."""
    from app.modules.organizations.models import OrganizationAdmin

    columns = set(OrganizationAdmin.__table__.columns.keys())
    assert "password_hash" not in columns
    assert "password" not in columns
