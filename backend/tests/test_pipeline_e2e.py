"""Pipeline against a real database, real storage and real Qdrant.

Gemini is not required: the AI stages degrade, and the document is still
chunked, embedded and indexed (docs/features/document-processing.md §9).

Qdrant must be running. These tests use the `documents_test` collection from
conftest — never the development one.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tests.conftest import make_tenant, reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

LEAVE_POLICY = (
    "Annual Leave Policy\n\n"
    "All permanent employees are entitled to twenty-four days of paid annual leave "
    "per calendar year. Leave accrues monthly and unused days may be carried "
    "forward for up to six months.\n\n"
    "Requests must be submitted at least two weeks in advance through the HR portal. "
    "Managers are expected to respond within three working days.\n"
)

EXPENSES = (
    "Expense Reimbursement Policy\n\n"
    "Claims must be submitted within thirty days of the expense being incurred. "
    "Receipts are required for every claim above one hundred rupees.\n"
)


def qdrant_running() -> bool:
    from app.vector.client import is_available

    return is_available()


pytestmark = pytest.mark.skipif(
    not qdrant_running(), reason="Qdrant is not running on QDRANT_URL"
)


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
    yield session
    session.close()


@pytest.fixture()
def storage():
    from app.storage.local import get_storage

    return get_storage()


@pytest.fixture(autouse=True)
def _clean(db):
    reset_database(db)

    yield


@pytest.fixture()
def tenant(db):
    """Release 2: a document belongs to an organization, not to the Super Admin."""
    return make_tenant(db, name="Pipeline", slug="pipeline",
                       email="pipeline@example.com")


@pytest.fixture()
def admin(tenant):
    return tenant[1]


@pytest.fixture()
def organization(tenant):
    return tenant[0]


def make_document(db, storage, admin, *, name: str, body: bytes, file_type: str):
    """Insert a QUEUED document with its blob, as upload would."""
    from app.modules.documents.repositories.document_repository import DocumentRepository
    from app.utils.hashing import sha256_stream

    digest, size = sha256_stream(io.BytesIO(body))
    key = f"test/{uuid4()}.{file_type}"
    storage.save(key, io.BytesIO(body))

    document = DocumentRepository(db).create(
        file_name=name,
        original_file_name=name,
        file_type=file_type,
        mime_type="text/plain" if file_type == "txt" else "application/pdf",
        file_size=size,
        storage_key=key,
        file_hash=digest,
        created_by=admin.id,
        organization_id=admin.organization_id,
    )
    db.commit()
    return document


@pytest.fixture()
def pipeline(db, storage):
    from app.modules.documents.services.pipeline_service import ProcessingPipeline

    return ProcessingPipeline(db, storage)


# --- Happy path --------------------------------------------------------- #


def test_txt_document_completes_and_is_indexed(db, storage, admin, pipeline):
    document = make_document(db, storage, admin, name="leave.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")

    result = pipeline.run(document.id)

    assert result.status == "COMPLETED"
    assert result.chunks > 0
    assert result.vectors == result.chunks

    db.refresh(document)
    assert str(document.status) == "COMPLETED"
    assert document.content_hash is not None
    assert document.page_count == 1

    from app.vector.repository import count_document_vectors

    assert count_document_vectors(document.organization_id, document.id) == result.chunks


def test_chunks_are_persisted_and_traceable(db, storage, admin, pipeline):
    document = make_document(db, storage, admin, name="leave.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")
    pipeline.run(document.id)

    rows = db.execute(
        text("""SELECT chunk_index, page_number, vector_point_id, char_count
                FROM document_chunks WHERE document_id = :d ORDER BY chunk_index"""),
        {"d": str(document.id)},
    ).all()

    assert rows
    # Every chunk traces back to document, page and index (§26).
    assert [r.chunk_index for r in rows] == list(range(len(rows)))
    assert all(r.page_number == 1 for r in rows)
    assert all(r.vector_point_id is not None for r in rows)
    assert all(r.char_count > 0 for r in rows)


def test_point_ids_match_between_postgres_and_qdrant(db, storage, admin, pipeline):
    document = make_document(db, storage, admin, name="leave.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")
    pipeline.run(document.id)

    from app.vector.client import point_id_for

    rows = db.execute(
        text("SELECT chunk_index, vector_point_id FROM document_chunks WHERE document_id = :d"),
        {"d": str(document.id)},
    ).all()

    for row in rows:
        assert str(row.vector_point_id) == point_id_for(document.id, row.chunk_index)


def test_search_finds_the_indexed_document(db, storage, admin, pipeline):
    document = make_document(db, storage, admin, name="leave.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")
    pipeline.run(document.id)

    from app.vector.embeddings import embed_text
    from app.vector.repository import search

    hits = search(document.organization_id, embed_text("How many days of annual leave do employees get?"))

    assert hits, "the indexed document should be retrievable"
    assert hits[0].payload["document_id"] == str(document.id)
    assert "twenty-four" in hits[0].payload["text"].lower()


def test_unrelated_query_returns_nothing(db, storage, admin, pipeline):
    """The score threshold is what makes a grounded refusal possible (§34)."""
    document = make_document(db, storage, admin, name="leave.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")
    pipeline.run(document.id)

    from app.vector.embeddings import embed_text
    from app.vector.repository import search

    hits = search(document.organization_id, embed_text("What is the boiling point of liquid nitrogen?"))
    assert hits == []


# --- Idempotency (§23) --------------------------------------------------- #


def test_reprocessing_does_not_duplicate_chunks_or_vectors(db, storage, admin, pipeline):
    document = make_document(db, storage, admin, name="leave.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")
    first = pipeline.run(document.id)

    # Reprocess: terminal -> QUEUED is the only legal re-entry.
    from app.modules.documents.models import DocumentStatus
    from app.modules.documents.services.state_machine import transition

    db.refresh(document)
    transition(db, document, DocumentStatus.QUEUED, stage="requeued")

    second = pipeline.run(document.id)

    assert second.status == "COMPLETED"
    assert second.chunks == first.chunks

    from app.vector.repository import count_document_vectors

    assert count_document_vectors(document.organization_id, document.id) == first.chunks

    count = db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id = :d"),
        {"d": str(document.id)},
    ).scalar_one()
    assert count == first.chunks


def test_reprocessing_shorter_content_leaves_no_orphans(db, storage, admin, pipeline):
    """A shortened document must not keep its old tail chunks (§29)."""
    long_body = (LEAVE_POLICY * 12).encode()
    document = make_document(db, storage, admin, name="leave.txt",
                             body=long_body, file_type="txt")
    first = pipeline.run(document.id)
    assert first.chunks > 2

    # Replace the blob with something much shorter, then reprocess.
    storage.delete(document.storage_key)
    storage.save(document.storage_key, io.BytesIO(b"Short replacement text."))

    from app.modules.documents.models import DocumentStatus
    from app.modules.documents.services.state_machine import transition

    db.refresh(document)
    transition(db, document, DocumentStatus.QUEUED, stage="requeued")
    second = pipeline.run(document.id)

    assert second.chunks < first.chunks

    from app.vector.repository import count_document_vectors

    assert count_document_vectors(document.organization_id, document.id) == second.chunks


# --- Content duplicate (ADR-005) ---------------------------------------- #


def test_content_duplicate_is_detected_and_not_indexed(db, storage, admin, pipeline):
    first = make_document(db, storage, admin, name="original.txt",
                          body=LEAVE_POLICY.encode(), file_type="txt")
    pipeline.run(first.id)

    # Same text, different bytes — passes level 1, caught at level 2.
    variant = ("  " + LEAVE_POLICY.replace("\n", "\r\n").upper() + "  ").encode()
    second = make_document(db, storage, admin, name="reformatted.txt",
                           body=variant, file_type="txt")
    result = pipeline.run(second.id)

    assert result.status == "DUPLICATE"
    assert result.duplicate_of == str(first.id)

    db.refresh(second)
    assert second.duplicate_of_document_id == first.id

    # Never indexed: a duplicate in Qdrant means the model sees the same
    # passage twice.
    from app.vector.repository import count_document_vectors

    assert count_document_vectors(second.organization_id, second.id) == 0
    assert db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id = :d"),
        {"d": str(second.id)},
    ).scalar_one() == 0


def test_different_documents_are_not_duplicates(db, storage, admin, pipeline):
    a = make_document(db, storage, admin, name="leave.txt",
                      body=LEAVE_POLICY.encode(), file_type="txt")
    pipeline.run(a.id)

    b = make_document(db, storage, admin, name="expenses.txt",
                      body=EXPENSES.encode(), file_type="txt")
    result = pipeline.run(b.id)

    assert result.status == "COMPLETED"


# --- Failure paths ------------------------------------------------------ #


def test_missing_blob_fails_cleanly(db, storage, admin, pipeline):
    document = make_document(db, storage, admin, name="gone.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")
    storage.delete(document.storage_key)

    result = pipeline.run(document.id)

    assert result.status == "FAILED"
    assert result.error_code == "STORAGE_FILE_MISSING"


def test_document_with_no_text_fails(db, storage, admin, pipeline):
    document = make_document(db, storage, admin, name="blank.txt",
                             body=b"   \n\n  ", file_type="txt")
    result = pipeline.run(document.id)

    assert result.status == "FAILED"
    assert result.error_code == "NO_TEXT_EXTRACTED"


def test_deleted_document_is_skipped(db, storage, admin, pipeline):
    """Deleted mid-flight: abort, index nothing."""
    document = make_document(db, storage, admin, name="leave.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")
    db.execute(text("UPDATE documents SET deleted_at = now(), status = 'DELETED' WHERE id = :d"),
               {"d": str(document.id)})
    db.commit()

    result = pipeline.run(document.id)
    assert result.status == "skipped"

    from app.vector.repository import count_document_vectors

    assert count_document_vectors(document.organization_id, document.id) == 0


def test_ai_stages_degrade_without_gemini(db, storage, admin, pipeline):
    """No provider configured: the document must still be indexed.

    Skipped when a provider IS active - the assertion is about the degraded path,
    and with a live provider the test would silently exercise the opposite one.
    """
    from app.modules.ai import llm_client

    if llm_client.is_configured():
        pytest.skip("A provider is active; this test covers the unconfigured path")

    document = make_document(db, storage, admin, name="leave.txt",
                             body=LEAVE_POLICY.encode(), file_type="txt")
    result = pipeline.run(document.id)

    assert result.status == "COMPLETED"
    assert result.degraded is True
    assert result.vectors > 0

    db.refresh(document)
    # Title falls back to the filename; category is simply unset.
    assert document.title == "leave.txt"
    assert document.category_id is None


# --- OCR (ADR-004) ------------------------------------------------------- #
#
# Until Tesseract was installed these paths could only be asserted as failures.
# Fixtures are generated, never committed: a synthetic image is reproducible and
# — for identity documents — carries no risk of a real card entering the repo.


def tesseract_ready() -> bool:
    from app.modules.documents.services.ocr_service import tesseract_available

    try:
        return tesseract_available()
    except Exception:
        return False


needs_tesseract = pytest.mark.skipif(
    not tesseract_ready(), reason="Tesseract is not installed"
)


def render_png(lines: list[str], *, width=1200, height=500) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:  # pragma: no cover
        font = ImageFont.load_default()

    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 64

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_scanned_pdf(pages: list[list[str]]) -> bytes:
    """No text layer — each page is an image, which is what forces OCR."""
    import pymupdf

    document = pymupdf.open()
    for lines in pages:
        page = document.new_page(width=612, height=792)
        page.insert_image(pymupdf.Rect(50, 50, 562, 263), stream=render_png(lines))
    out = document.tobytes()
    document.close()
    return out


@needs_tesseract
def test_an_image_is_ocred_chunked_and_indexed(db, storage, admin, pipeline):
    """The whole point of OCR: an image becomes searchable text."""
    body = render_png(["Annual Leave Policy", "Twenty four days of paid leave"])
    document = make_document(db, storage, admin, name="policy.png",
                             body=body, file_type="png")

    result = pipeline.run(document.id)

    assert result.status == "COMPLETED", result.error_code
    assert result.ocr_pages == 1
    assert result.chunks > 0

    from app.vector.repository import count_document_vectors

    assert count_document_vectors(document.organization_id, document.id) == result.chunks

    recognised = db.execute(
        text("SELECT string_agg(text, ' ') FROM document_chunks WHERE document_id = :d"),
        {"d": str(document.id)},
    ).scalar_one()
    assert "leave" in recognised.lower()


@needs_tesseract
def test_a_scanned_pdf_triggers_ocr_and_keeps_page_numbers(db, storage, admin, pipeline):
    """Page numbers must survive OCR, or a citation points at the wrong page."""
    body = render_scanned_pdf([["Alpha section about leave"], ["Bravo section about expenses"]])
    document = make_document(db, storage, admin, name="scan.pdf",
                             body=body, file_type="pdf")

    result = pipeline.run(document.id)

    assert result.status == "COMPLETED", result.error_code
    assert result.ocr_pages == 2
    assert result.pages == 2

    rows = db.execute(
        text("""SELECT page_number, text FROM document_chunks
                WHERE document_id = :d ORDER BY chunk_index"""),
        {"d": str(document.id)},
    ).all()

    by_page = {row.page_number: row.text.lower() for row in rows}
    assert "alpha" in by_page[1]
    assert "bravo" in by_page[2]


@needs_tesseract
def test_a_digital_pdf_never_reaches_ocr(db, storage, admin, pipeline):
    """OCR is the slowest stage. A PDF with a real text layer must skip it."""
    import pymupdf

    document_pdf = pymupdf.open()
    page = document_pdf.new_page()
    page.insert_text((72, 200), "Expense Reimbursement Policy. " * 12, fontsize=12)
    body = document_pdf.tobytes()
    document_pdf.close()

    document = make_document(db, storage, admin, name="digital.pdf",
                             body=body, file_type="pdf")

    result = pipeline.run(document.id)

    assert result.status == "COMPLETED", result.error_code
    assert result.ocr_pages == 0, "a text-layer PDF must not be rasterised"


@needs_tesseract
def test_an_ocred_image_is_retrievable_by_meaning(db, storage, admin, pipeline):
    """End to end: a photograph of a policy answers a question about it."""
    body = render_png(["Annual Leave Policy",
                       "Employees receive twenty four days of paid leave"])
    document = make_document(db, storage, admin, name="policy.png",
                             body=body, file_type="png")
    assert pipeline.run(document.id).status == "COMPLETED"

    from app.vector.embeddings import embed_text
    from app.vector.repository import search

    hits = search(document.organization_id, embed_text("How many days of annual leave do employees get?"))

    assert hits, "an OCRed image should be retrievable"
    assert hits[0].payload["document_id"] == str(document.id)


@needs_tesseract
def test_a_blank_scan_fails_rather_than_indexing_nothing(db, storage, admin, pipeline):
    """Empty OCR output must not become an indexed empty document — it would
    poison the content hash and match every future blank page (ADR-004)."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (900, 500), "white").save(buffer, format="PNG")

    document = make_document(db, storage, admin, name="blank.png",
                             body=buffer.getvalue(), file_type="png")

    result = pipeline.run(document.id)

    assert result.status == "FAILED"
    assert result.error_code == "NO_TEXT_EXTRACTED"
