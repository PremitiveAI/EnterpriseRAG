"""Text extraction (spec §22 stage 3, docs/features/document-processing.md).

Deterministic — no AI (spec §8).

Page and slide boundaries are preserved here because that is the only place
they exist. Losing them means chunks cannot carry a ``page_number``, and the
"Page 4" citation chip in the chat UI becomes impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import BinaryIO

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import get_logger
from config.settings import settings

logger = get_logger(__name__)


class ExtractionError(AppError):
    status_code = 422
    error_code = ErrorCode.EXTRACTION_FAILED
    message = "The document could not be read."


@dataclass
class Page:
    number: int
    text: str
    section: str | None = None
    needs_ocr: bool = False


@dataclass
class Extraction:
    pages: list[Page] = field(default_factory=list)
    page_count: int = 0
    # True when at least one page yielded too little text to be believable.
    requires_ocr: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text.strip())

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


# --- PDF ---------------------------------------------------------------- #


def extract_pdf(stream: BinaryIO) -> Extraction:
    import pymupdf

    data = stream.read()
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ExtractionError("The PDF could not be opened.") from exc

    if document.needs_pass:
        document.close()
        raise ExtractionError("The PDF is password-protected.")

    pages: list[Page] = []
    try:
        for index, page in enumerate(document, start=1):
            text = (page.get_text() or "").strip()
            # A page with almost no text is a scan, not an empty page.
            needs_ocr = len(text) < settings.OCR_MIN_CHARS_PER_PAGE
            pages.append(Page(number=index, text=text, needs_ocr=needs_ocr))
        count = document.page_count
    finally:
        document.close()

    return Extraction(
        pages=pages,
        page_count=count,
        requires_ocr=any(p.needs_ocr for p in pages),
    )


# --- DOCX --------------------------------------------------------------- #


def extract_docx(stream: BinaryIO) -> Extraction:
    from docx import Document as DocxDocument

    try:
        document = DocxDocument(stream)
    except Exception as exc:
        raise ExtractionError("The Word document could not be read.") from exc

    parts: list[str] = []
    section: str | None = None

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        # Headings become the section label carried onto chunks.
        if paragraph.style is not None and (paragraph.style.name or "").startswith("Heading"):
            section = text
        parts.append(text)

    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    body = "\n".join(parts)
    # Word has no fixed page concept without rendering, so the whole document
    # is one logical page. Citations fall back to the section heading.
    return Extraction(pages=[Page(number=1, text=body, section=section)], page_count=1)


# --- PPTX --------------------------------------------------------------- #


def extract_pptx(stream: BinaryIO) -> Extraction:
    from pptx import Presentation

    try:
        presentation = Presentation(stream)
    except Exception as exc:
        raise ExtractionError("The presentation could not be read.") from exc

    pages: list[Page] = []
    for index, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        title: str | None = None

        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            if not text:
                continue
            if title is None:
                title = text.splitlines()[0][:255]
            parts.append(text)

        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
            if notes:
                parts.append(f"[Notes] {notes}")

        # A slide is a page — that is what makes "slide 7" citable.
        pages.append(Page(number=index, text="\n".join(parts), section=title))

    return Extraction(pages=pages, page_count=len(pages))


# --- TXT ---------------------------------------------------------------- #


def extract_txt(stream: BinaryIO) -> Extraction:
    data = stream.read()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 cannot fail
        raise ExtractionError("The text file's encoding could not be determined.")

    return Extraction(pages=[Page(number=1, text=text.strip())], page_count=1)


# --- Images ------------------------------------------------------------- #


def extract_image(_stream: BinaryIO) -> Extraction:
    """Images carry no text layer — everything comes from OCR."""
    return Extraction(pages=[Page(number=1, text="", needs_ocr=True)], page_count=1,
                      requires_ocr=True)


EXTRACTORS = {
    "pdf": extract_pdf,
    "docx": extract_docx,
    "pptx": extract_pptx,
    "txt": extract_txt,
    "png": extract_image,
    "jpg": extract_image,
    "jpeg": extract_image,
    "webp": extract_image,
}


def extract(file_type: str, stream: BinaryIO) -> Extraction:
    extractor = EXTRACTORS.get(file_type.lower())
    if extractor is None:
        raise ExtractionError(f"No extractor for .{file_type}.")

    extraction = extractor(stream)
    logger.info(
        "Extracted",
        extra={
            "file_type": file_type,
            "pages": extraction.page_count,
            "chars": extraction.char_count,
            "requires_ocr": extraction.requires_ocr,
        },
    )
    return extraction
