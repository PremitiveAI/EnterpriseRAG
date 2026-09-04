"""OCR via Tesseract (ADR-004, spec §22 stage 4).

Conditional, not universal: a PDF with a real text layer never reaches here.
Deterministic — the agent that reads identity cards reasons over this output,
it does not perform the OCR itself (spec §8).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from functools import lru_cache
from typing import BinaryIO

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import get_logger
from config.settings import settings

logger = get_logger(__name__)

# Rendering a PDF page for OCR. 300 DPI is the usual floor for reliable results.
OCR_DPI = 300


class OCRUnavailable(AppError):
    status_code = 503
    error_code = ErrorCode.OCR_UNAVAILABLE
    message = "OCR is unavailable: Tesseract is not installed or not on PATH."


class OCRFailed(AppError):
    status_code = 422
    error_code = ErrorCode.OCR_FAILED
    message = "OCR failed."


@dataclass
class OCRResult:
    text: str
    pages_processed: int


@lru_cache(maxsize=1)
def tesseract_available() -> bool:
    """Capability check, cached for the life of the process."""
    import pytesseract

    if settings.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

    binary = settings.TESSERACT_CMD or shutil.which("tesseract")
    if not binary:
        return False

    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _preprocess(image):
    """Greyscale, denoise and threshold.

    Not cosmetic — this measurably changes accuracy on scans, which is the only
    input OCR ever sees here.
    """
    import cv2
    import numpy as np

    array = np.array(image)
    if array.ndim == 3:
        array = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)

    array = cv2.fastNlMeansDenoising(array, h=10)
    return cv2.adaptiveThreshold(
        array, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )


def ocr_image_bytes(data: bytes) -> str:
    import io

    import pytesseract
    from PIL import Image

    if not tesseract_available():
        raise OCRUnavailable()

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            processed = _preprocess(image)
            return (pytesseract.image_to_string(processed, lang=settings.OCR_LANGUAGE) or "").strip()
    except OCRUnavailable:
        raise
    except Exception as exc:
        raise OCRFailed(f"OCR failed: {type(exc).__name__}") from exc


def ocr_pdf_pages(stream: BinaryIO, page_numbers: list[int]) -> dict[int, str]:
    """Rasterise the named 1-based pages and OCR each."""
    import pymupdf
    import pytesseract
    from PIL import Image

    if not tesseract_available():
        raise OCRUnavailable()

    stream.seek(0)
    document = pymupdf.open(stream=stream.read(), filetype="pdf")
    results: dict[int, str] = {}

    try:
        matrix = pymupdf.Matrix(OCR_DPI / 72, OCR_DPI / 72)
        for number in page_numbers:
            if number < 1 or number > document.page_count:
                continue
            pixmap = document[number - 1].get_pixmap(matrix=matrix)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            processed = _preprocess(image)
            results[number] = (
                pytesseract.image_to_string(processed, lang=settings.OCR_LANGUAGE) or ""
            ).strip()
    except Exception as exc:
        raise OCRFailed(f"OCR failed: {type(exc).__name__}") from exc
    finally:
        document.close()

    logger.info("OCR complete", extra={"pages_processed": len(results)})
    return results
