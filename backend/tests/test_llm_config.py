"""The versioned configuration cache (ADR-010 §3-§5).

Phase 3. The happy paths run against the real database, because "a second
process sees the change" is the claim being made and an in-memory fake would
prove nothing about it. The failure paths use a session whose ``execute``
raises, which is the only honest way to test an unreachable database without
making one unreachable.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core import crypto
from app.core.exceptions import LLMConfigUnavailableError, NoActiveLLMProviderError
from app.modules.ai import llm_config
from config.settings import settings

BACKEND_ROOT = Path(__file__).resolve().parent.parent

API_KEY = "AIzaSyD-fake-test-credential-000000000000"


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
    session.execute(text("DELETE FROM llm_providers"))
    session.commit()
    llm_config.reset_cache()
    yield session
    session.rollback()
    session.close()
    llm_config.reset_cache()


def _activate(db, *, provider: str = "gemini", model: str = "gemini-2.0-flash",
              key: str = API_KEY, config: str = "{}") -> None:
    """Make one row the active one, replacing whatever was active."""
    stored = crypto.encrypt(key)
    db.execute(text("UPDATE llm_providers SET is_active = false WHERE is_active"))
    db.execute(
        text(
            """
            INSERT INTO llm_providers
                (provider_name, model_name, encrypted_api_key, encryption_key_id,
                 key_fingerprint, config, is_active)
            VALUES (:p, :m, :t, :k, :f, CAST(:c AS jsonb), true)
            """
        ),
        {"p": provider, "m": model, "t": stored.token, "k": stored.key_id,
         "f": stored.fingerprint, "c": config},
    )
    db.commit()


class _DeadSession:
    """A session whose every statement fails, as one does when the database is
    unreachable or the connection was closed underneath it."""

    def execute(self, *_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


# --- Reading ---------------------------------------------------------------- #


def test_the_active_provider_is_resolved_and_decrypted(db):
    _activate(db, provider="gemini", model="gemini-2.0-flash")

    config = llm_config.get_config(db)

    assert (config.provider, config.model) == ("gemini", "gemini-2.0-flash")
    assert config.api_key == API_KEY
    assert config.fingerprint == crypto.fingerprint(API_KEY)


def test_json_config_becomes_params(db):
    _activate(db, config='{"temperature": 0.2, "timeout_seconds": 30}')

    config = llm_config.get_config(db)

    assert config.params == {"temperature": 0.2, "timeout_seconds": 30}


def test_an_unchanged_version_returns_the_very_same_object(db):
    """Not merely an equal one. The call-site client caches key on identity, so
    a new object per call would rebuild a CrewAI client on every message."""
    _activate(db)

    first = llm_config.get_config(db)
    second = llm_config.get_config(db)

    assert first is second


def test_a_new_version_produces_a_new_config(db):
    """The end-to-end claim of the feature: a switch takes effect with no
    restart and no coordination between processes."""
    _activate(db, provider="gemini", model="gemini-2.0-flash")
    before = llm_config.get_config(db)

    _activate(db, provider="openai", model="gpt-4o")
    after = llm_config.get_config(db)

    assert after is not before
    assert after.version > before.version
    assert (after.provider, after.model) == ("openai", "gpt-4o")


def test_replacing_only_the_credential_still_rebuilds(db):
    """A rotated key reaches every process, not just a changed model."""
    _activate(db, key=API_KEY)
    before = llm_config.get_config(db)

    _activate(db, key="a-completely-different-credential")
    after = llm_config.get_config(db)

    assert after is not before
    assert after.api_key == "a-completely-different-credential"


def test_no_active_provider_is_a_503_and_not_a_silent_fallback(db):
    """The failure the seed migration exists to prevent. Falling back to a
    templated answer here would hide the one thing a Super Admin must see."""
    with pytest.raises(NoActiveLLMProviderError) as excinfo:
        llm_config.get_config(db)

    assert excinfo.value.status_code == 503
    assert excinfo.value.error_code == "LLM_NO_ACTIVE_PROVIDER"


def test_a_deactivated_provider_does_not_survive_in_the_cache(db):
    """Deactivating must not leave a working config behind that a later database
    outage could resurrect through last-known-good."""
    _activate(db)
    llm_config.get_config(db)

    db.execute(text("UPDATE llm_providers SET is_active = false"))
    db.commit()

    with pytest.raises(NoActiveLLMProviderError):
        llm_config.get_config(db)

    # ...and now that nothing is cached, an outage cannot serve one either.
    with pytest.raises(LLMConfigUnavailableError):
        llm_config.get_config(_DeadSession())


# --- An unreachable database ------------------------------------------------ #


def test_a_cold_process_fails_closed(db):
    """Nothing to fall back to. Availability is not on offer here."""
    with pytest.raises(LLMConfigUnavailableError) as excinfo:
        llm_config.get_config(_DeadSession())

    assert excinfo.value.status_code == 503


def test_a_brief_outage_serves_the_last_known_good_configuration(db):
    """A pool timeout or a failover must not fail a chat, and in the worker it
    must not throw away minutes of extraction and OCR."""
    _activate(db)
    warm = llm_config.get_config(db)

    served = llm_config.get_config(_DeadSession())

    assert served is warm
    assert llm_config.is_serving_stale() is True


def test_a_long_outage_stops_serving(db):
    """The other half of the bargain. A revoked credential must stop working in
    bounded time even when the database cannot say so."""
    _activate(db)
    llm_config.get_config(db)

    # Age the cache past the ceiling rather than waiting five minutes.
    llm_config._CACHE["confirmed_at"] = (
        time.monotonic() - settings.LLM_CONFIG_STALENESS_SECONDS - 1
    )

    with pytest.raises(LLMConfigUnavailableError):
        llm_config.get_config(_DeadSession())


def test_the_boundary_is_the_configured_ceiling(db, monkeypatch):
    """Just inside serves; just outside refuses. Asserted against the setting so
    the two branches cannot drift apart."""
    monkeypatch.setattr(settings, "LLM_CONFIG_STALENESS_SECONDS", 60, raising=False)
    _activate(db)
    llm_config.get_config(db)

    llm_config._CACHE["confirmed_at"] = time.monotonic() - 59
    assert llm_config.get_config(_DeadSession()) is not None

    llm_config._CACHE["confirmed_at"] = time.monotonic() - 61
    with pytest.raises(LLMConfigUnavailableError):
        llm_config.get_config(_DeadSession())


def test_recovering_clears_the_degraded_flag(db):
    """Otherwise every later answer would be labelled degraded for the life of
    the process."""
    _activate(db)
    llm_config.get_config(db)
    llm_config.get_config(_DeadSession())
    assert llm_config.is_serving_stale() is True

    llm_config.get_config(db)

    assert llm_config.is_serving_stale() is False


# --- A readable database with an unusable credential ------------------------ #


def test_an_undecryptable_credential_fails_immediately(db, monkeypatch):
    """Not an availability problem. The database answered; the credential is
    wrong, altered, or written under a key this host no longer has. None of
    those improve by waiting, so last-known-good does not apply."""
    _activate(db)
    monkeypatch.setattr(
        settings, "ENCRYPTION_KEYS", "1:a-completely-different-test-key-value", raising=False
    )

    with pytest.raises(LLMConfigUnavailableError):
        llm_config.get_config(db)


def test_an_undecryptable_credential_does_not_fall_back_to_the_cache(db, monkeypatch):
    """The dangerous version of the test above: a warm process must not keep
    answering on an old configuration after the stored one became unreadable."""
    _activate(db)
    llm_config.get_config(db)

    _activate(db, key="rotated-credential-value")
    monkeypatch.setattr(
        settings, "ENCRYPTION_KEYS", "1:a-completely-different-test-key-value", raising=False
    )

    with pytest.raises(LLMConfigUnavailableError):
        llm_config.get_config(db)


# --- Nothing leaks the credential ------------------------------------------- #


def test_the_repr_never_contains_the_key(db):
    """This object reaches log lines and exception context."""
    _activate(db)

    config = llm_config.get_config(db)

    assert API_KEY not in repr(config)
    assert config.fingerprint in repr(config)


def test_health_reports_the_provider_and_never_the_key(db):
    _activate(db, provider="gemini", model="gemini-2.0-flash")
    llm_config.get_config(db)

    described = llm_config.describe()

    assert described["provider"] == "gemini"
    assert described["model"] == "gemini-2.0-flash"
    assert described["key_fingerprint"] == crypto.fingerprint(API_KEY)
    assert API_KEY not in str(described)


def test_health_says_so_when_nothing_is_configured(db):
    """'No provider configured' and 'running provider X' must be different
    answers, not one absent field."""
    assert llm_config.describe() == {"provider": None, "model": None, "configured": False}


def test_health_does_not_touch_the_database(db, monkeypatch):
    """A health check that queries the database becomes the thing that fails.

    Asserted by making any attempt to open a session an error, rather than by
    trusting that the implementation does not.
    """
    _activate(db)
    llm_config.get_config(db)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("describe() opened a database session")

    monkeypatch.setattr("app.core.database.SessionLocal", _forbidden)

    assert llm_config.describe()["configured"] is True
