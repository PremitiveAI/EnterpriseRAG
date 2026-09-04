"""Credential encryption (ADR-010 §6).

Phase 1: the encryption foundation, before anything stores a credential.

Every test here points the key registry at throwaway secrets via monkeypatch.
Nothing touches the database, and nothing reads the real ``ENCRYPTION_KEYS`` -
a suite that encrypted under the developer's live key would write test rows
that only that host could ever read.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core import crypto
from config.settings import (
    MIN_ENCRYPTION_SECRET_CHARS,
    Settings,
    parse_encryption_keys,
    settings,
)

KEY_ONE = "test-key-one-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
KEY_TWO = "test-key-two-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

API_KEY = "AIzaSyD-fake-test-credential-000000000000"


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    """Two keys configured, the first active."""
    monkeypatch.setattr(
        settings, "ENCRYPTION_KEYS", f"1:{KEY_ONE},2:{KEY_TWO}", raising=False
    )
    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", 1, raising=False)
    yield


# --- Round trip ------------------------------------------------------------- #


def test_a_credential_survives_a_round_trip():
    stored = crypto.encrypt(API_KEY)

    assert crypto.decrypt(stored.token) == API_KEY


def test_the_ciphertext_never_contains_the_plaintext():
    """The one thing the whole module exists to guarantee."""
    stored = crypto.encrypt(API_KEY)

    assert API_KEY not in stored.token
    assert API_KEY not in stored.fingerprint


def test_the_same_credential_encrypts_differently_every_time():
    """A random nonce per record. Without it, two rows holding the same key
    would be visibly identical to anyone reading the table."""
    first = crypto.encrypt(API_KEY)
    second = crypto.encrypt(API_KEY)

    assert first.token != second.token
    assert crypto.decrypt(first.token) == crypto.decrypt(second.token) == API_KEY


def test_an_empty_credential_is_refused():
    """It would encrypt and decrypt perfectly and then fail at the provider with
    an error pointing nowhere near here."""
    for empty in ("", "   ", "\n"):
        with pytest.raises(ValueError):
            crypto.encrypt(empty)


# --- The failures that must be loud ---------------------------------------- #


def test_the_wrong_key_raises_instead_of_returning_garbage(monkeypatch):
    """The reason the scheme is AES-256-GCM and not "AES-256".

    Under an unauthenticated mode this call returns plausible bytes and the
    credential goes to the provider as nonsense. Here it must raise.
    """
    stored = crypto.encrypt(API_KEY)
    monkeypatch.setattr(
        settings, "ENCRYPTION_KEYS", f"1:{'z' * 44}", raising=False
    )

    with pytest.raises(crypto.DecryptionFailedError):
        crypto.decrypt(stored.token)


def test_a_tampered_ciphertext_raises():
    """Authentication, not just confidentiality: an altered row is detected
    rather than decrypted into something else."""
    stored = crypto.encrypt(API_KEY)
    scheme, key_id, blob = stored.token.split(".")
    flipped = ("A" if blob[-1] != "A" else "B")
    tampered = f"{scheme}.{key_id}.{blob[:-1]}{flipped}"

    with pytest.raises((crypto.DecryptionFailedError, crypto.MalformedCiphertextError)):
        crypto.decrypt(tampered)


def test_a_ciphertext_naming_an_unconfigured_key_says_so(monkeypatch):
    """The failure mode that the key id column exists to make recoverable.

    Without the id, this row would fail authentication with no way to tell "the
    secret is wrong" from "the secret is elsewhere".
    """
    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", 2, raising=False)
    stored = crypto.encrypt(API_KEY)
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", f"1:{KEY_ONE}", raising=False)

    with pytest.raises(crypto.UnknownEncryptionKeyError) as excinfo:
        crypto.decrypt(stored.token)

    assert "key id 2" in str(excinfo.value)


@pytest.mark.parametrize(
    "token",
    [
        "not-a-token",
        "v1.1",
        "v1.1.$$$not-base64$$$",
        "v2.1.YWJj",
        "v1.notanint.YWJj",
        "v1.1.YWJj",  # valid base64, far too short to hold a nonce
    ],
)
def test_a_value_this_module_did_not_produce_is_rejected(token):
    with pytest.raises(crypto.MalformedCiphertextError):
        crypto.decrypt(token)


def test_nothing_can_be_read_or_written_without_a_registry(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", None, raising=False)

    assert crypto.is_configured() is False
    with pytest.raises(crypto.EncryptionNotConfiguredError):
        crypto.encrypt(API_KEY)
    with pytest.raises(crypto.EncryptionNotConfiguredError):
        crypto.decrypt("v1.1.YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo")


# --- Rotation --------------------------------------------------------------- #


def test_a_credential_written_before_a_rotation_still_decrypts_after_it(monkeypatch):
    """The whole point of shipping the key id column now rather than later."""
    written_under_key_one = crypto.encrypt(API_KEY)
    assert written_under_key_one.key_id == 1

    # The rotation: key 2 becomes active, key 1 stays listed.
    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", 2, raising=False)
    written_under_key_two = crypto.encrypt(API_KEY)

    assert written_under_key_two.key_id == 2
    assert crypto.decrypt(written_under_key_two.token) == API_KEY
    # ...and the old row is untouched and still readable.
    assert crypto.decrypt(written_under_key_one.token) == API_KEY


def test_the_key_a_row_needs_is_readable_without_decrypting_it():
    """How a re-encryption job finds its work."""
    stored = crypto.encrypt(API_KEY)

    assert crypto.key_id_of(stored.token) == stored.key_id == crypto.active_key_id()


# --- Fingerprint ------------------------------------------------------------ #


def test_the_fingerprint_identifies_the_credential_not_the_ciphertext():
    """It is taken over the plaintext, so the same key entered twice matches
    even though the two ciphertexts differ. That is what makes the activation
    conflict check work across a rotation."""
    first = crypto.encrypt(API_KEY)
    second = crypto.encrypt(API_KEY)

    assert first.fingerprint == second.fingerprint
    assert first.token != second.token


def test_a_different_credential_has_a_different_fingerprint():
    assert crypto.fingerprint(API_KEY) != crypto.fingerprint(API_KEY + "x")


def test_the_fingerprint_is_a_fixed_width_hex_label():
    """Stored as VARCHAR(16), so a longer one would be silently truncated."""
    fp = crypto.fingerprint(API_KEY)

    assert len(fp) == crypto.FINGERPRINT_CHARS == 16
    assert all(c in "0123456789abcdef" for c in fp)


# --- The key registry ------------------------------------------------------- #


def test_an_unset_registry_is_empty_rather_than_an_error():
    """An unconfigured host is not a misconfigured host. Only the second should
    stop the process."""
    assert parse_encryption_keys(None) == {}
    assert parse_encryption_keys("   ") == {}


def test_several_keys_parse_and_whitespace_is_tolerated():
    keys = parse_encryption_keys(f" 1 : {KEY_ONE} , 2:{KEY_TWO} ")

    assert keys == {1: KEY_ONE, 2: KEY_TWO}


def test_a_secret_may_contain_a_colon():
    """Only the first separates the id, so a base64 secret is safe."""
    secret = "a:b" + "c" * 40

    assert parse_encryption_keys(f"1:{secret}") == {1: secret}


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (f"{KEY_ONE}", "no ':' separator"),
        (f"one:{KEY_ONE}", "is not an integer"),
        (f"0:{KEY_ONE}", "must be >= 1"),
        (f"1:{KEY_ONE},1:{KEY_TWO}", "more than once"),
        ("1:too-short", "at least"),
    ],
)
def test_a_broken_registry_is_rejected_with_the_reason(raw, reason):
    """Every one of these would otherwise surface much later, as a failure to
    save a credential."""
    with pytest.raises(ValueError) as excinfo:
        parse_encryption_keys(raw)

    assert reason in str(excinfo.value)


def test_the_minimum_secret_length_matches_the_jwt_rule():
    """A key derived from a short string is a short key however it is stretched,
    which is the same reason JWT_SECRET has this floor."""
    assert MIN_ENCRYPTION_SECRET_CHARS == 32

    with pytest.raises(ValueError):
        parse_encryption_keys("1:" + "x" * (MIN_ENCRYPTION_SECRET_CHARS - 1))

    assert parse_encryption_keys("1:" + "x" * MIN_ENCRYPTION_SECRET_CHARS)


def test_an_active_key_id_naming_nothing_is_refused(monkeypatch):
    """Settings rejects this at import. Reached here by mutating the registry at
    runtime, which is the only way the guard can be exercised."""
    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", 9, raising=False)

    with pytest.raises(crypto.UnknownEncryptionKeyError):
        crypto.encrypt(API_KEY)


# --- The one hard failure -------------------------------------------------- #
#
# Everything else in this file degrades or raises at the point of use. These two
# stop the process at import, which is the only way a misconfigured registry
# cannot be discovered months later as a failure to save a credential.


def _env(monkeypatch, **overrides: str) -> None:
    """Enough environment to construct Settings without reading the real .env.

    Set explicitly so the test does not pass or fail on whether the developer's
    .env happens to exist.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
    monkeypatch.setenv("JWT_SECRET", "j" * 48)
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)


def test_settings_refuses_to_load_when_the_active_key_names_nothing(monkeypatch):
    """The process must not start. A registry whose active id resolves to no
    secret cannot encrypt anything, and every write would fail at the moment a
    Super Admin tries to save a credential - long after the mistake was made."""
    _env(monkeypatch, ENCRYPTION_KEYS=f"1:{KEY_ONE}", ENCRYPTION_ACTIVE_KEY_ID="7")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "ENCRYPTION_ACTIVE_KEY_ID=7" in str(excinfo.value)


def test_settings_refuses_to_load_on_a_malformed_registry(monkeypatch):
    """Same hard failure, reached through the parser rather than the cross-check."""
    _env(monkeypatch, ENCRYPTION_KEYS="1:too-short")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_a_valid_registry_loads_and_parses(monkeypatch):
    """The other half of the guard: it must not reject a correct configuration."""
    _env(
        monkeypatch,
        ENCRYPTION_KEYS=f"1:{KEY_ONE},2:{KEY_TWO}",
        ENCRYPTION_ACTIVE_KEY_ID="2",
    )

    loaded = Settings(_env_file=None)  # type: ignore[call-arg]

    assert loaded.encryption_keys() == {1: KEY_ONE, 2: KEY_TWO}
    assert loaded.ENCRYPTION_ACTIVE_KEY_ID == 2


def test_an_unconfigured_host_still_starts(monkeypatch):
    """An absent registry is not a broken one. The API must come up so it can
    report that it has no encryption configured, which is the same rule main.py
    applies to Redis and Qdrant."""
    _env(monkeypatch)
    monkeypatch.delenv("ENCRYPTION_KEYS", raising=False)

    loaded = Settings(_env_file=None)  # type: ignore[call-arg]

    assert loaded.encryption_keys() == {}
