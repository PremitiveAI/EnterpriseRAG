"""Content hashing (spec §18).

Level 1 (SHA-256 of raw bytes) runs synchronously at upload. Level 2
(SHA-256 of normalised text) runs in the Celery task after extraction — see
docs/architecture/decisions/ADR-005-duplicate-detection-split.md.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import BinaryIO

CHUNK_BYTES = 64 * 1024

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏﻿]")
_WHITESPACE = re.compile(r"\s+")


def sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    """Hash a stream in blocks. Returns ``(hex_digest, byte_count)``.

    Streamed rather than read whole: a 20 MB upload should never sit in memory
    twice. The caller is responsible for the stream position afterwards.
    """
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalise_text(text: str) -> str:
    """Canonical form used for the level-2 content hash.

    The point of normalisation is that the same document exported to a
    different format hashes identically: the same policy saved from Word to PDF
    has a different file_hash and an identical content_hash.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip().lower()


def content_hash(text: str) -> str:
    return sha256_bytes(normalise_text(text).encode("utf-8"))
