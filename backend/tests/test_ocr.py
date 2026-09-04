"""OCR against the real Tesseract binary (ADR-004, spec §22 stage 4).

Until Tesseract was installed, only the *degradation* path was tested — every
assertion about OCR was really an assertion about it being unavailable. These
run the actual binary.

Fixtures are **generated**, not committed: a synthetic image with known text is
reproducible, reviewable in the test that builds it, and — for the identity-card
case — carries no risk of a real PAN or Aadhaar entering the repository.

Skipped when Tesseract is absent, so the suite still passes on a machine
without it.
"""

from __future__ import annotations

import io

import pytest

from app.modules.documents.services import ocr_service


def tesseract_ready() -> bool:
    try:
        return ocr_service.tesseract_available()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not tesseract_ready(),
    reason="Tesseract is not installed or TESSERACT_CMD is not set",
)


# --- Generated fixtures -------------------------------------------------- #


def render_text_image(lines: list[str], *, width=1000, height=400, size=40) -> bytes:
    """A clean black-on-white PNG. Deliberately easy input: this suite tests
    that the plumbing works, not how Tesseract copes with bad scans."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype("arial.ttf", size)
    except OSError:  # pragma: no cover - depends on the host's fonts
        font = ImageFont.load_default()

    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += size + 20

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_scanned_pdf(pages: list[list[str]]) -> bytes:
    """A PDF with NO text layer — each page is an embedded image.

    This is the input that makes OCR necessary. A PDF built from text would
    have a text layer and never reach the OCR stage at all.
    """
    import pymupdf

    document = pymupdf.open()
    for lines in pages:
        png = render_text_image(lines, width=1200, height=500)
        page = document.new_page(width=612, height=792)
        page.insert_image(pymupdf.Rect(50, 50, 562, 263), stream=png)

    out = document.tobytes()
    document.close()
    return out


def normalise(text: str) -> str:
    return " ".join(text.split()).lower()


# --- Capability ---------------------------------------------------------- #


def test_the_binary_is_reachable():
    import pytesseract

    assert pytesseract.get_tesseract_version() is not None


def test_the_configured_language_pack_is_installed():
    """`eng` missing would fail every OCR with an unhelpful error."""
    import pytesseract

    from config.settings import settings

    assert settings.OCR_LANGUAGE in pytesseract.get_languages(config="")


# --- Images -------------------------------------------------------------- #


def test_text_is_read_back_from_an_image():
    data = render_text_image(["Annual Leave Policy", "Twenty four days per year"])

    text = normalise(ocr_service.ocr_image_bytes(data))

    assert "annual leave policy" in text
    assert "twenty four days" in text


def test_digits_survive_ocr():
    """Identity documents are mostly digits; a transposition matters (§18)."""
    data = render_text_image(["Employee ID 4827", "Extension 91055"])

    text = ocr_service.ocr_image_bytes(data)

    assert "4827" in text
    assert "91055" in text


def test_a_blank_image_yields_empty_text_not_an_error():
    """Empty is a legitimate result. It must not be confused with a failure —
    the pipeline decides what an empty page means, not this module."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (600, 300), "white").save(buffer, format="PNG")

    assert ocr_service.ocr_image_bytes(buffer.getvalue()).strip() == ""


def test_corrupt_image_bytes_raise_ocr_failed_not_unavailable():
    """The two codes mean different things: one is 'fix your install', the
    other is 'this file is bad'."""
    with pytest.raises(ocr_service.OCRFailed):
        ocr_service.ocr_image_bytes(b"this is not an image")


# --- Scanned PDFs -------------------------------------------------------- #


def test_a_scanned_pdf_page_is_read():
    pdf = render_scanned_pdf([["Expense Reimbursement Policy"]])

    results = ocr_service.ocr_pdf_pages(io.BytesIO(pdf), [1])

    assert set(results) == {1}
    assert "expense reimbursement" in normalise(results[1])


def test_only_the_requested_pages_are_processed():
    """OCR is the slowest stage; a digital page must never be rasterised
    needlessly (ADR-004)."""
    pdf = render_scanned_pdf([["Page one text"], ["Page two text"], ["Page three text"]])

    results = ocr_service.ocr_pdf_pages(io.BytesIO(pdf), [2])

    assert set(results) == {2}
    assert "page two" in normalise(results[2])


def test_page_numbers_are_one_based_and_map_correctly():
    """An off-by-one here would cite the wrong page in an answer (§35)."""
    pdf = render_scanned_pdf([["Alpha section"], ["Bravo section"]])

    results = ocr_service.ocr_pdf_pages(io.BytesIO(pdf), [1, 2])

    assert "alpha" in normalise(results[1])
    assert "bravo" in normalise(results[2])


def test_out_of_range_pages_are_skipped_silently():
    pdf = render_scanned_pdf([["Only page"]])

    results = ocr_service.ocr_pdf_pages(io.BytesIO(pdf), [0, 1, 99])

    assert set(results) == {1}


def test_preprocessing_runs_and_returns_a_usable_image():
    """Greyscale, denoise and threshold measurably change accuracy; a silent
    failure here would degrade every scan."""
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(render_text_image(["Sample"]))).convert("RGB")
    processed = ocr_service._preprocess(image)

    assert isinstance(processed, np.ndarray)
    assert processed.ndim == 2, "should be single-channel after greyscale"
    assert set(np.unique(processed)).issubset({0, 255}), "threshold should be binary"
