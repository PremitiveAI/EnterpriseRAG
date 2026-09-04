"""Symmetric encryption for stored credentials (ADR-010 §6).

Used by ``llm_providers`` to hold provider API keys. Nothing else encrypts
anything today, and this module deliberately exposes no general-purpose "encrypt
this blob" surface beyond what that needs.

Three properties are load-bearing, and each exists because the obvious weaker
version fails silently:

**Authenticated encryption, not just AES-256.** A key size is not a scheme.
Under CBC-without-a-MAC or ECB, decrypting with the *wrong* key returns
plausible garbage rather than raising, and the guarantee that a malformed
credential is never sent to a provider then has no failure to detect. AES-GCM
turns that into an exception, which is the whole point.

**A key registry, not a key.** Every ciphertext names the key it was written
under, so two keys can coexist while a rotation is in progress. The re-encryption
job is deferred (ADR-010 §6); the ability to write it is not.

**A fingerprint over the plaintext.** Lets the UI, the audit log and the
activation conflict check compare two credentials without decrypting or
displaying either.

Errors here are internal. They are NOT ``AppError`` subclasses, because a
decryption failure must never be rendered to a client as a message describing
the state of the key registry; the callers in later phases map them to
``LLM_CONFIG_UNAVAILABLE``.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from config.settings import settings

# Bumping this means a new token format. Old tokens keep their own prefix and
# must keep decrypting, so a change here is additive, never a rename.
SCHEME = "v1"

NONCE_BYTES = 12  # GCM's standard nonce length; anything else costs a rehash
KEY_BYTES = 32  # AES-256
FINGERPRINT_CHARS = 16

# Domain separation for the derived key. If this string changes, every existing
# ciphertext becomes undecryptable, so it is a constant and not a setting.
_HKDF_INFO = b"EnterpriseRAG:credential-encryption:v1"


class CryptoError(Exception):
    """Base for every failure in this module."""


class EncryptionNotConfiguredError(CryptoError):
    """No key registry is configured, so nothing can be read or written."""


class UnknownEncryptionKeyError(CryptoError):
    """The ciphertext names a key id the registry does not contain.

    This is the failure a rotation done *without* the key id column would have
    produced silently and unrecoverably.
    """


class MalformedCiphertextError(CryptoError):
    """The stored value is not a token this module produced."""


class DecryptionFailedError(CryptoError):
    """The key is wrong, or the ciphertext was altered.

    GCM cannot distinguish the two, and for the caller they mean the same thing:
    do not send this credential anywhere.
    """


@dataclass(frozen=True)
class EncryptedSecret:
    """Everything a caller must persist for one credential.

    ``key_id`` duplicates what is already inside ``token``. That is deliberate:
    the token is authoritative for *decrypting*, and the column exists so a
    rotation job can find the rows that still need re-encrypting without
    decrypting every one of them first.
    """

    token: str
    key_id: int
    fingerprint: str


def fingerprint(plaintext: str) -> str:
    """A stable, non-reversible label for a credential.

    Over the plaintext, so the same key entered twice produces the same
    fingerprint no matter which encryption key each row was written under - that
    is what makes it usable for the activation conflict check across a rotation.

    Truncated to 64 bits. It is a change-detector, not a security boundary, and
    it is never exposed outside a Super Admin context.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]


def encrypt(plaintext: str) -> EncryptedSecret:
    """Encrypt under the *active* key."""
    if not plaintext or not plaintext.strip():
        # An empty credential encrypts and decrypts perfectly well, and then
        # fails at the provider with an error that points nowhere near here.
        raise ValueError("Refusing to encrypt an empty secret.")

    key_id, secret = _active_key()
    nonce = os.urandom(NONCE_BYTES)
    blob = AESGCM(_derive(secret)).encrypt(nonce, plaintext.encode("utf-8"), None)

    return EncryptedSecret(
        token=f"{SCHEME}.{key_id}.{_b64encode(nonce + blob)}",
        key_id=key_id,
        fingerprint=fingerprint(plaintext),
    )


def decrypt(token: str) -> str:
    """Decrypt under whichever key the token names - not the active one.

    A token written before a rotation must keep working after it, which is the
    entire reason the key id travels with the ciphertext.
    """
    key_id, blob = _parse(token)
    registry = _registry()

    if key_id not in registry:
        raise UnknownEncryptionKeyError(
            f"Ciphertext was written under key id {key_id}, which is not in "
            "ENCRYPTION_KEYS. The secret it needs has been removed or was never "
            "configured on this host."
        )

    if len(blob) <= NONCE_BYTES:
        raise MalformedCiphertextError("Ciphertext is too short to contain a nonce.")

    nonce, payload = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        plaintext = AESGCM(_derive(registry[key_id])).decrypt(nonce, payload, None)
    except InvalidTag as exc:
        raise DecryptionFailedError(
            f"Credential encrypted under key id {key_id} failed authentication: "
            "the configured secret is wrong, or the stored value was altered."
        ) from exc

    return plaintext.decode("utf-8")


def key_id_of(token: str) -> int:
    """The key a stored token needs, without decrypting it."""
    return _parse(token)[0]


def active_key_id() -> int:
    return _active_key()[0]


def is_configured() -> bool:
    """Whether encryption can be used at all.

    Deliberately not a startup check: an absent registry is a host that has not
    finished being configured, and the API must still start so it can say so —
    the rule ``main.py`` already applies to Redis and Qdrant. A *malformed*
    registry is the opposite case and does stop the process, in
    ``Settings._encryption_registry_is_usable``.

    No caller yet. ``/health`` reports this from phase 7.
    """
    return bool(settings.encryption_keys())


# --- internals -------------------------------------------------------------- #


def _registry() -> dict[int, str]:
    keys = settings.encryption_keys()
    if not keys:
        raise EncryptionNotConfiguredError(
            "ENCRYPTION_KEYS is not set, so stored credentials cannot be read or "
            "written. Add it to .env - see .env.example."
        )
    return keys


def _active_key() -> tuple[int, str]:
    registry = _registry()
    key_id = settings.ENCRYPTION_ACTIVE_KEY_ID
    if key_id not in registry:
        # Settings rejects this at import; reachable only if the registry is
        # mutated at runtime, which tests do.
        raise UnknownEncryptionKeyError(
            f"ENCRYPTION_ACTIVE_KEY_ID={key_id} is not present in ENCRYPTION_KEYS."
        )
    return key_id, registry[key_id]


@lru_cache(maxsize=8)
def _derive(secret: str) -> bytes:
    """Stretch a configured secret into a 32-byte AES key.

    HKDF rather than a raw hash so the key is domain-separated from any other
    use of the same secret. Deterministic by necessity - a random salt would
    make yesterday's ciphertext unreadable.

    Cached because it runs on the credential read path, which sits in front of
    every LLM call.
    """
    return HKDF(
        algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=_HKDF_INFO
    ).derive(secret.encode("utf-8"))


def _parse(token: str) -> tuple[int, bytes]:
    if not isinstance(token, str):
        raise MalformedCiphertextError("Ciphertext must be a string.")

    parts = token.split(".")
    if len(parts) != 3:
        raise MalformedCiphertextError("Ciphertext is not in <scheme>.<key id>.<data> form.")

    scheme, raw_id, data = parts
    if scheme != SCHEME:
        raise MalformedCiphertextError(f"Unsupported ciphertext scheme {scheme!r}.")

    try:
        key_id = int(raw_id)
    except ValueError as exc:
        raise MalformedCiphertextError(f"Key id {raw_id!r} is not an integer.") from exc

    try:
        blob = _b64decode(data)
    except Exception as exc:  # binascii raises several distinct types
        raise MalformedCiphertextError("Ciphertext is not valid base64.") from exc

    return key_id, blob


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
