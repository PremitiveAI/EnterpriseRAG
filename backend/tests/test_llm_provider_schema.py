"""The ``llm_providers`` schema and its seed migration (ADR-010).

Phase 2. Everything asserted here is enforced by PostgreSQL rather than by
application code, so the tests talk to the database directly: an ORM-level check
would prove only that this code path behaves, not that the constraint exists.

Deliberately does **not** use ``reset_database``. That helper drops Qdrant
collections, and nothing in this module has any use for a vector store.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.core import crypto
from config.settings import settings

BACKEND_ROOT = Path(__file__).resolve().parent.parent

API_KEY = "AIzaSyD-fake-test-credential-000000000000"


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="module", autouse=True)
def _schema():
    command.upgrade(_alembic_config(), "head")
    yield


@pytest.fixture()
def db():
    from app.core.database import SessionLocal

    session = SessionLocal()
    session.execute(text("DELETE FROM llm_providers"))
    session.commit()
    yield session
    session.rollback()
    session.close()


def _insert(db, *, active: bool = False, model: str = "gemini-2.0-flash",
            provider: str = "gemini", key: str = API_KEY) -> dict:
    stored = crypto.encrypt(key)
    row = db.execute(
        text(
            """
            INSERT INTO llm_providers
                (provider_name, model_name, encrypted_api_key,
                 encryption_key_id, key_fingerprint, is_active)
            VALUES (:provider, :model, :token, :key_id, :fingerprint, :active)
            RETURNING id, config_version
            """
        ),
        {
            "provider": provider, "model": model, "token": stored.token,
            "key_id": stored.key_id, "fingerprint": stored.fingerprint,
            "active": active,
        },
    ).one()
    db.commit()
    return {"id": row[0], "version": row[1], "stored": stored}


# --- One active provider ---------------------------------------------------- #


def test_a_second_active_provider_is_rejected_by_the_database(db):
    """The constraint that makes two concurrent activations impossible. In
    application code this is a check-then-write race; here the loser gets an
    error and there is no state in which two providers are live."""
    _insert(db, active=True, provider="gemini")

    with pytest.raises(IntegrityError):
        _insert(db, active=True, provider="openai")

    db.rollback()


def test_any_number_of_inactive_providers_is_fine(db):
    """The index is partial. Registering providers is not the same as running
    them, and a Super Admin must be able to prepare one before switching."""
    _insert(db, provider="gemini")
    _insert(db, provider="openai")
    _insert(db, provider="anthropic")

    assert db.execute(text("SELECT count(*) FROM llm_providers")).scalar_one() == 3


def test_deactivating_before_activating_is_what_makes_a_switch_work(db):
    """Order is not arbitrary. The unique index is checked per statement, so
    activating the new row first collides with the row about to be stood down -
    which is why the activation flow deactivates first."""
    old = _insert(db, active=True, provider="gemini")
    new = _insert(db, provider="openai")

    db.execute(text("UPDATE llm_providers SET is_active = false WHERE id = :id"),
               {"id": old["id"]})
    db.execute(text("UPDATE llm_providers SET is_active = true WHERE id = :id"),
               {"id": new["id"]})
    db.commit()

    active = db.execute(
        text("SELECT provider_name FROM llm_providers WHERE is_active")
    ).scalar_one()
    assert active == "openai"


def test_activating_first_collides(db):
    """The failure the ordering above avoids, asserted rather than assumed."""
    _insert(db, active=True, provider="gemini")
    new = _insert(db, provider="openai")

    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE llm_providers SET is_active = true WHERE id = :id"),
                   {"id": new["id"]})
        db.commit()

    db.rollback()


# --- The version ------------------------------------------------------------ #


def test_versions_are_monotonic_across_rows_not_within_them(db):
    """The silent-collision guard.

    With per-row counters these three rows would all be version 1, and a process
    warm on one of them would compare equal against another and never rebuild -
    answering from the wrong provider with nothing raised anywhere.
    """
    versions = [
        _insert(db, provider="gemini")["version"],
        _insert(db, provider="openai")["version"],
        _insert(db, provider="anthropic")["version"],
    ]

    assert versions == sorted(versions)
    assert len(set(versions)) == 3


def test_changing_the_configuration_bumps_the_version(db):
    row = _insert(db, provider="gemini")

    db.execute(
        text("UPDATE llm_providers SET model_name = 'gemini-2.5-pro' WHERE id = :id"),
        {"id": row["id"]},
    )
    db.commit()

    after = db.execute(
        text("SELECT config_version FROM llm_providers WHERE id = :id"), {"id": row["id"]}
    ).scalar_one()
    assert after > row["version"]


def test_replacing_the_credential_bumps_the_version(db):
    """A rotated key must reach every process, not only a changed model."""
    row = _insert(db, provider="gemini")
    replacement = crypto.encrypt("a-different-credential-entirely")

    db.execute(
        text("UPDATE llm_providers SET encrypted_api_key = :t, key_fingerprint = :f "
             "WHERE id = :id"),
        {"t": replacement.token, "f": replacement.fingerprint, "id": row["id"]},
    )
    db.commit()

    after = db.execute(
        text("SELECT config_version FROM llm_providers WHERE id = :id"), {"id": row["id"]}
    ).scalar_one()
    assert after > row["version"]


def test_bookkeeping_writes_do_not_bump_the_version(db):
    """`last_tested_at` says nothing about what the config resolves to. Bumping
    on it would make every process rebuild a client that did not change, each
    time a Super Admin pressed Test."""
    row = _insert(db, provider="gemini")

    db.execute(text("UPDATE llm_providers SET last_tested_at = now() WHERE id = :id"),
               {"id": row["id"]})
    db.commit()

    after = db.execute(
        text("SELECT config_version FROM llm_providers WHERE id = :id"), {"id": row["id"]}
    ).scalar_one()
    assert after == row["version"]


def test_a_deactivated_row_also_takes_a_new_version(db):
    """Both sides of a switch move, so a process cached on the outgoing row
    rebuilds too."""
    row = _insert(db, active=True, provider="gemini")

    db.execute(text("UPDATE llm_providers SET is_active = false WHERE id = :id"),
               {"id": row["id"]})
    db.commit()

    after = db.execute(
        text("SELECT config_version FROM llm_providers WHERE id = :id"), {"id": row["id"]}
    ).scalar_one()
    assert after > row["version"]


# --- The credential --------------------------------------------------------- #


def test_a_credential_survives_the_round_trip_through_the_column(db):
    """TEXT, not bytea: the token is base64 in a dotted envelope, and a column
    type that mangled it would fail as a decryption error much later."""
    row = _insert(db, provider="gemini")

    token, key_id = db.execute(
        text("SELECT encrypted_api_key, encryption_key_id FROM llm_providers "
             "WHERE id = :id"),
        {"id": row["id"]},
    ).one()

    assert crypto.decrypt(token) == API_KEY
    assert key_id == crypto.active_key_id()


def test_the_stored_value_is_not_the_credential(db):
    _insert(db, provider="gemini")

    stored = db.execute(text("SELECT encrypted_api_key FROM llm_providers")).scalar_one()

    assert API_KEY not in stored
    assert stored.startswith("v1.")


def test_the_fingerprint_column_holds_the_whole_fingerprint(db):
    """VARCHAR(16) against a 16-character value: one character narrower and
    every comparison in the activation flow would fail on a truncation."""
    row = _insert(db, provider="gemini")

    fingerprint = db.execute(
        text("SELECT key_fingerprint FROM llm_providers WHERE id = :id"),
        {"id": row["id"]},
    ).scalar_one()

    assert fingerprint == row["stored"].fingerprint
    assert len(fingerprint) == 16


def test_an_unknown_provider_name_is_rejected(db):
    """An enum, so a typo is a database error rather than a row that resolves to
    no client at call time."""
    with pytest.raises((DataError, IntegrityError)):
        _insert(db, provider="gemeni")

    db.rollback()


# --- The seed --------------------------------------------------------------- #


def test_the_migration_seeds_the_provider_from_the_environment(db, monkeypatch):
    """The step that prevents an upgrade from being a silent outage.

    Also exercises `downgrade` — the schema is torn down and rebuilt here, so a
    migration that could not be reversed would fail this test rather than be
    discovered during a rollback.
    """
    cfg = _alembic_config()
    monkeypatch.setenv("GEMINI_API_KEY", API_KEY)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")

    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    provider, model, token, active = db.execute(
        text("SELECT provider_name, model_name, encrypted_api_key, is_active "
             "FROM llm_providers")
    ).one()

    assert (provider, model, active) == ("gemini", "gemini-2.0-flash", True)
    assert crypto.decrypt(token) == API_KEY


def test_an_environment_with_no_key_seeds_nothing(db, monkeypatch):
    """A host that had no working AI before the migration still has none after
    it, and says so - rather than the migration inventing a row."""
    cfg = _alembic_config()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    assert db.execute(text("SELECT count(*) FROM llm_providers")).scalar_one() == 0


def test_a_key_with_no_encryption_registry_refuses_to_migrate(db, monkeypatch):
    """The one case that must fail loudly. Seeding would mean either storing the
    credential in plaintext or dropping it silently."""
    cfg = _alembic_config()
    monkeypatch.setenv("GEMINI_API_KEY", API_KEY)
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", None, raising=False)

    command.downgrade(cfg, "-1")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEYS"):
        command.upgrade(cfg, "head")

    # The failed upgrade rolled back, so the schema is a revision behind. Put it
    # back rather than leaving the test database for the next module to find.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    command.upgrade(cfg, "head")
