"""Validation and hashing — the §46 upload list, without a database."""

import io

import pytest

from app.core.error_codes import ErrorCode
from app.utils.file_validation import (
    ValidationFailure,
    extension_of,
    looks_like_text,
    sanitise_filename,
    signature_matches,
    validate_content,
    validate_metadata,
)
from app.utils.hashing import content_hash, normalise_text, sha256_bytes, sha256_stream

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"\x00" * 8
DOCX = b"PK\x03\x04" + b"\x00" * 16
TXT = b"Employee handbook, page 4.\n"


class TestFilenameSanitisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "../../../etc/passwd",
            r"..\..\windows\system32\config",
            r"C:\Users\admin\secret.pdf",
            "/absolute/path/file.pdf",
        ],
    )
    def test_path_components_are_stripped(self, raw):
        safe = sanitise_filename(raw)
        assert "/" not in safe and "\\" not in safe
        assert ".." not in safe
        assert not safe.startswith("C:")

    def test_control_characters_removed(self):
        assert "\x00" not in sanitise_filename("bad\x00name.pdf")

    def test_unicode_preserved(self):
        assert sanitise_filename("रिपोर्ट-2024.pdf") == "रिपोर्ट-2024.pdf"

    def test_long_name_truncated_to_255_bytes(self):
        safe = sanitise_filename("a" * 400 + ".pdf")
        assert len(safe.encode("utf-8")) <= 255

    def test_empty_name_is_empty(self):
        assert sanitise_filename("   ") == ""

    def test_extension_extraction(self):
        assert extension_of("report.final.PDF") == "pdf"
        assert extension_of("noextension") == ""


class TestMetadataValidation:
    def test_supported_extension_accepted(self):
        assert validate_metadata("report.pdf") == ("report.pdf", "pdf")

    def test_unsupported_extension_rejected(self):
        result = validate_metadata("diagram.bmp")
        assert isinstance(result, ValidationFailure)
        assert result.error_code is ErrorCode.INVALID_FILE_TYPE

    @pytest.mark.parametrize("name", ["legacy.doc", "deck.ppt"])
    def test_legacy_formats_get_their_own_code(self, name):
        """A product decision, not an unrecognised format (§15 deviation)."""
        result = validate_metadata(name)
        assert isinstance(result, ValidationFailure)
        assert result.error_code is ErrorCode.LEGACY_FORMAT_UNSUPPORTED
        assert "docx" in result.message or "pptx" in result.message

    def test_no_extension_rejected(self):
        result = validate_metadata("README")
        assert isinstance(result, ValidationFailure)
        assert result.error_code is ErrorCode.INVALID_FILE_TYPE

    def test_traversal_name_still_validated_on_its_extension(self):
        assert validate_metadata("../../evil.pdf") == ("evil.pdf", "pdf")


class TestSignatures:
    @pytest.mark.parametrize(
        "ext,head",
        [("pdf", PDF), ("png", PNG), ("jpg", JPG), ("jpeg", JPG),
         ("webp", WEBP), ("docx", DOCX), ("pptx", DOCX)],
    )
    def test_genuine_signatures_accepted(self, ext, head):
        assert signature_matches(ext, head)

    def test_renamed_png_is_caught(self):
        """A PNG uploaded as .pdf must not pass."""
        assert not signature_matches("pdf", PNG)

    def test_renamed_executable_is_caught(self):
        assert not signature_matches("pdf", b"MZ\x90\x00" + b"\x00" * 12)

    def test_wav_not_accepted_as_webp(self):
        """RIFF alone is not enough — WAV shares it."""
        wav = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVE" + b"\x00" * 8
        assert not signature_matches("webp", wav)

    def test_txt_has_no_signature_requirement(self):
        assert signature_matches("txt", TXT)

    def test_binary_rejected_as_text(self):
        assert not looks_like_text(b"\x00\x01\x02binary")

    def test_utf8_accepted_as_text(self):
        assert looks_like_text("नमस्ते".encode())


class TestContentValidation:
    def test_valid_pdf_passes(self):
        assert validate_content("pdf", PDF, 1024) is None

    def test_empty_file_rejected(self):
        failure = validate_content("pdf", PDF, 0)
        assert failure.error_code is ErrorCode.FILE_EMPTY

    def test_oversized_document_rejected(self):
        failure = validate_content("pdf", PDF, 21 * 1024 * 1024)
        assert failure.error_code is ErrorCode.FILE_TOO_LARGE
        assert failure.details["limit_bytes"] == 20 * 1024 * 1024

    def test_document_limit_is_inclusive(self):
        assert validate_content("pdf", PDF, 20 * 1024 * 1024) is None

    def test_images_use_the_smaller_limit(self):
        failure = validate_content("png", PNG, 3 * 1024 * 1024)
        assert failure.error_code is ErrorCode.FILE_TOO_LARGE
        assert failure.details["limit_bytes"] == 2 * 1024 * 1024

    def test_signature_mismatch_rejected(self):
        failure = validate_content("pdf", PNG, 1024)
        assert failure.error_code is ErrorCode.FILE_SIGNATURE_MISMATCH


class TestHashing:
    def test_stream_hash_matches_bytes_hash(self):
        data = b"x" * 200_000
        digest, size = sha256_stream(io.BytesIO(data))
        assert digest == sha256_bytes(data)
        assert size == len(data)

    def test_empty_stream(self):
        digest, size = sha256_stream(io.BytesIO(b""))
        assert size == 0
        assert digest == sha256_bytes(b"")

    def test_identical_bytes_hash_identically(self):
        assert sha256_bytes(b"same") == sha256_bytes(b"same")


class TestNormalisation:
    def test_whitespace_collapsed(self):
        assert normalise_text("a   b\n\n\tc") == "a b c"

    def test_case_folded(self):
        assert normalise_text("Leave Policy") == "leave policy"

    def test_line_ending_differences_hash_the_same(self):
        """A file converted between platforms is still the same document."""
        assert content_hash("line one\r\nline two") == content_hash("line one\nline two")

    def test_zero_width_characters_removed(self):
        assert content_hash("po\u200blicy") == content_hash("policy")

    def test_different_text_hashes_differently(self):
        assert content_hash("policy a") != content_hash("policy b")
