"""Server-side file validation (spec §17).

Never trusts anything the client says. Extension, MIME type and file signature
are derived from the bytes; the client's ``Content-Type`` header is ignored
entirely.

Signatures are checked from a curated table rather than via libmagic, which
avoids an external binary dependency on Windows for the handful of formats
this system accepts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from app.core.error_codes import ErrorCode
from config.settings import settings

# --- Filenames ---------------------------------------------------------- #

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DOTS = re.compile(r"\.{2,}")
MAX_FILENAME_BYTES = 255


def sanitise_filename(raw: str) -> str:
    """Strip every path component and unsafe character.

    The result is used for display and to derive an extension. It is NEVER used
    to build a filesystem path — storage keys are generated (spec §45).
    """
    name = unicodedata.normalize("NFKC", raw or "").strip()

    # Take the final component under both path flavours: a Windows client may
    # send "C:\\Users\\me\\file.pdf" and a POSIX one "/home/me/file.pdf".
    name = PureWindowsPath(PurePosixPath(name).name).name

    name = _UNSAFE.sub("_", name)
    name = _DOTS.sub(".", name).strip(". ")

    while len(name.encode("utf-8")) > MAX_FILENAME_BYTES:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:-1] + dot + ext) if dot else name[:-1]

    return name


def extension_of(filename: str) -> str:
    _, _, ext = filename.rpartition(".")
    return ext.lower() if ext and ext != filename else ""


# --- Signatures --------------------------------------------------------- #


@dataclass(frozen=True)
class Signature:
    offset: int
    magic: bytes


# Extension -> acceptable signatures. TXT has none by design.
SIGNATURES: dict[str, tuple[Signature, ...]] = {
    "pdf": (Signature(0, b"%PDF-"),),
    # OOXML files are ZIP containers. "PK\x03\x04" is a populated archive;
    # the other two markers appear in empty or spanned archives.
    "docx": (Signature(0, b"PK\x03\x04"), Signature(0, b"PK\x05\x06"), Signature(0, b"PK\x07\x08")),
    "pptx": (Signature(0, b"PK\x03\x04"), Signature(0, b"PK\x05\x06"), Signature(0, b"PK\x07\x08")),
    "png": (Signature(0, b"\x89PNG\r\n\x1a\n"),),
    "jpg": (Signature(0, b"\xff\xd8\xff"),),
    "jpeg": (Signature(0, b"\xff\xd8\xff"),),
    # RIFF....WEBP — the size field sits between the two markers.
    "webp": (Signature(0, b"RIFF"),),
}

MIME_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

SIGNATURE_PEEK_BYTES = 16


def signature_matches(extension: str, head: bytes) -> bool:
    """True when the leading bytes are consistent with the extension."""
    expected = SIGNATURES.get(extension)
    if expected is None:
        # TXT and anything signature-less: nothing to verify here.
        return True

    if extension == "webp":
        # RIFF at 0 and WEBP at 8; checking RIFF alone would accept a WAV.
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"

    return any(head[s.offset : s.offset + len(s.magic)] == s.magic for s in expected)


def looks_like_text(sample: bytes) -> bool:
    """Heuristic for TXT, which has no signature to check."""
    if b"\x00" in sample:
        return False
    for encoding in ("utf-8", "utf-16", "cp1252"):
        try:
            sample.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


# --- Result ------------------------------------------------------------- #


@dataclass
class ValidationFailure:
    error_code: ErrorCode
    message: str
    details: dict[str, object] | None = None


@dataclass
class ValidatedFile:
    original_name: str
    safe_name: str
    extension: str
    mime_type: str
    size_bytes: int


def validate_metadata(raw_filename: str) -> tuple[str, str] | ValidationFailure:
    """Filename and extension checks. Returns ``(safe_name, extension)``."""
    safe = sanitise_filename(raw_filename)
    if not safe:
        return ValidationFailure(
            ErrorCode.INVALID_FILENAME, "The filename is empty or unusable."
        )

    ext = extension_of(safe)
    if not ext:
        return ValidationFailure(
            ErrorCode.INVALID_FILE_TYPE, "The file has no extension.", {"file_name": safe}
        )

    if ext in settings.LEGACY_EXTENSIONS:
        # A distinct code, because this is a deliberate product decision rather
        # than an unrecognised format (§15 deviation; ADR).
        return ValidationFailure(
            ErrorCode.LEGACY_FORMAT_UNSUPPORTED,
            f".{ext} is not supported. Save it as "
            f"{'.docx' if ext == 'doc' else '.pptx'} and upload again.",
            {"file_name": safe, "extension": ext},
        )

    if ext not in settings.allowed_extensions:
        return ValidationFailure(
            ErrorCode.INVALID_FILE_TYPE,
            f".{ext} is not a supported format.",
            {"file_name": safe, "extension": ext,
             "supported": sorted(settings.allowed_extensions)},
        )

    return safe, ext


def validate_content(extension: str, head: bytes, size_bytes: int) -> ValidationFailure | None:
    """Signature and size checks. Returns None when the file is acceptable."""
    if size_bytes == 0:
        return ValidationFailure(ErrorCode.FILE_EMPTY, "The file is empty.")

    limit = settings.size_limit_for(extension)
    if size_bytes > limit:
        return ValidationFailure(
            ErrorCode.FILE_TOO_LARGE,
            f"File exceeds the {limit // (1024 * 1024)} MB limit for "
            f"{'images' if extension in settings.ALLOWED_IMAGE_EXTENSIONS else 'documents'}.",
            {"size_bytes": size_bytes, "limit_bytes": limit},
        )

    if extension == "txt":
        if not looks_like_text(head):
            return ValidationFailure(
                ErrorCode.INVALID_FILE, "The file does not appear to be plain text."
            )
        return None

    if not signature_matches(extension, head):
        return ValidationFailure(
            ErrorCode.FILE_SIGNATURE_MISMATCH,
            f"The file contents do not match its .{extension} extension.",
            {"extension": extension},
        )

    return None
