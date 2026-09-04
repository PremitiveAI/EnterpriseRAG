"""Test environment.

Set BEFORE any application import so `config.settings` builds against the test
database rather than the development one.

The connection URL is derived from the real .env by swapping only the database
name. That way the suite uses the developer's actual credentials without them
being duplicated here, and it can never accidentally point at a database the
developer cares about — the name is always overridden.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values

BACKEND_ROOT = Path(__file__).resolve().parent.parent
TEST_DB_NAME = "enterprise_rag_test"


def _test_database_url() -> str:
    env = dotenv_values(BACKEND_ROOT / ".env")
    source = os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""

    if not source:
        return f"postgresql+psycopg://postgres:postgres@localhost:5432/{TEST_DB_NAME}"

    parts = urlsplit(source)
    return urlunsplit(parts._replace(path=f"/{TEST_DB_NAME}"))


# Override, not setdefault: a stray DATABASE_URL in the shell must not be able
# to aim the suite at a real database.
os.environ["DATABASE_URL"] = _test_database_url()

os.environ.setdefault("JWT_SECRET", "test-only-secret-value-not-for-any-real-use-32chars")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("QDRANT_COLLECTION", "documents_test")

# Override for the same reason as DATABASE_URL above: a suite that encrypted
# under the developer's live key would write rows only that host could ever
# read, and would put a real secret's derivative into a test database.
os.environ["ENCRYPTION_KEYS"] = "1:test-only-encryption-key-not-for-real-use"
os.environ["ENCRYPTION_ACTIVE_KEY_ID"] = "1"
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("STORAGE_ROOT", str(BACKEND_ROOT / "storage" / "_test"))


# ---------------------------------------------------------------------------
# Test cleanup order.
#
# Children before parents. `message_sources.document_id` is ON DELETE RESTRICT
# — deliberately, so a citation in a year-old answer still resolves — which
# means deleting `documents` before clearing the chat tables raises
# RestrictViolation. Defined once here rather than per suite: three separate
# copies of this list is how the chat tables came to be missing from three of
# them.
# ---------------------------------------------------------------------------

TRUNCATION_ORDER: tuple[str, ...] = (
    "message_sources",
    "chat_messages",
    "conversations",
    "audit_logs",
    "document_chunks",
    "document_tags",
    "document_processing",
    "documents",
    "document_categories",
    # Release 2. otp_requests references organization_admins, which references
    # organizations - and documents/conversations hold RESTRICT on both, so the
    # tenant tables come last.
    "otp_requests",
    "organization_admins",
    "organizations",
    "users",
)


def reset_database(session) -> None:
    """Empty every table a test can write, in dependency order.

    Also clears the rate-limit counters. They are per-user and per-IP, and a
    suite that uploads thirty files in a second would otherwise exhaust the
    20/minute budget and fail with 429s that say nothing about the code under
    test.
    """
    from sqlalchemy import text

    for table in TRUNCATION_ORDER:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()

    reset_rate_limits()
    drop_test_collections(session)


def drop_test_collections(session) -> None:
    """Drop Qdrant collections whose organization no longer exists.

    Every test tenant provisions a collection, and truncating PostgreSQL does
    not touch Qdrant. Without this, each run leaves its collections behind:
    48 of them accumulated to 5.7 GB and filled the disk, which surfaced as
    OSError deep inside unrelated tests.

    Only orphans are dropped - a collection whose organization row still exists
    is left alone, so this can never remove live data.
    """
    from sqlalchemy import text

    try:
        from app.vector.client import get_client

        live = {
            f"org_{str(row).replace('-', '')}"
            for row in session.execute(text("SELECT id FROM organizations")).scalars()
        }
        client = get_client()
        for collection in client.get_collections().collections:
            if collection.name.startswith("org_") and collection.name not in live:
                client.delete_collection(collection.name)
    except Exception:
        # Qdrant absent or unreachable: nothing to clean, and a cleanup failure
        # must not fail the test that triggered it.
        pass


def reset_rate_limits() -> None:
    """Drop every rate-limit counter. Never called on a request path."""
    try:
        from app.cache.redis_client import get_redis

        client = get_redis()
        for key in client.scan_iter("ratelimit:*"):
            client.delete(key)
    except Exception:
        # Redis absent: the limiter fails open, so there is nothing to clear.
        pass


# ---------------------------------------------------------------------------
# Release 2 tenancy helpers.
#
# Documents and conversations now belong to an Organization Admin, not to the
# Super Admin. Suites written for Release 1 created a `users` row and used it as
# the owner; these give them a tenant instead, in one place rather than five.
# ---------------------------------------------------------------------------


def make_tenant(session, *, name="Acme", slug="acme", email="admin@acme.example",
                mobile=None):
    """An organization plus one Organization Admin. Returns ``(org, admin)``."""
    import secrets

    from app.modules.organizations.repositories.admin_repository import (
        OrganizationAdminRepository,
    )
    from app.modules.organizations.repositories.organization_repository import (
        OrganizationRepository,
    )

    organization = OrganizationRepository(session).create(
        name=name, slug=slug, public_chat_key=secrets.token_urlsafe(32)
    )
    session.commit()

    admin = OrganizationAdminRepository(session).create(
        organization_id=organization.id,
        full_name=f"{name} Admin",
        email=email,
        mobile=mobile,
    )
    session.commit()
    return organization, admin


def otp_login(client, email: str) -> dict[str, str]:
    """Log an Organization Admin in and return the Authorization header.

    The OTP is the fixed development code; the request is rate limited per
    identifier, so a suite that logs in many times must reset between tests -
    `reset_database` clears `otp_requests` for exactly that reason.
    """
    client.post("/api/v1/auth/otp/request", json={"identifier": email})
    response = client.post(
        "/api/v1/auth/otp/verify", json={"identifier": email, "otp": "1111"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
