"""Document management against a real database (spec §28-§30).

Tests that touch vectors are skipped when Qdrant is not running; everything
else runs against PostgreSQL alone.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import make_tenant, otp_login, reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

BASE = "/api/v1/admin/documents"
TXT = b"Annual leave policy. Employees receive twenty-four days.\n"


def qdrant_running() -> bool:
    from app.vector.client import is_available

    return is_available()


needs_qdrant = pytest.mark.skipif(
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


@pytest.fixture(autouse=True)
def _clean(db):
    reset_database(db)
    yield


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def tenant(db):
    """The organization these tests operate inside.

    Release 2: documents and conversations belong to an Organization Admin, so
    the Release 1 Super Admin is no longer a valid owner.
    """
    return make_tenant(db, email="admin@example.com")


@pytest.fixture()
def auth(client, tenant):
    """A signed-in Organization Admin, returning the Authorization header."""
    return otp_login(client, "admin@example.com")


@pytest.fixture()
def admin(tenant):
    """The Organization Admin row - the owner of anything these tests create."""
    return tenant[1]


@pytest.fixture()
def storage():
    from app.storage.local import get_storage

    return get_storage()


@pytest.fixture()
def categories(db):
    from app.modules.documents.models import DocumentCategory

    rows = [
        DocumentCategory(slug="hr-policies", name="HR Policies", sort_order=10),
        DocumentCategory(slug="finance", name="Finance", sort_order=20),
        DocumentCategory(slug="retired", name="Retired", sort_order=30, is_active=False),
    ]
    db.add_all(rows)
    db.commit()
    return {row.slug: row for row in rows}


# --- Fixtures that build documents directly ------------------------------ #


def make_document(
    db,
    storage,
    admin,
    *,
    name="leave.txt",
    status="COMPLETED",
    title=None,
    category=None,
    document_type=None,
    language="en",
    tags=(),
    chunks=0,
    body=TXT,
    with_blob=True,
):
    """Insert a document in a chosen state, bypassing the pipeline."""
    from app.modules.documents.models import (
        Document,
        DocumentChunk,
        DocumentProcessing,
        DocumentStatus,
        DocumentTag,
        TagSource,
    )
    from app.utils.hashing import sha256_stream

    digest, size = sha256_stream(io.BytesIO(body))
    key = f"test/{uuid4()}.txt"
    if with_blob:
        storage.save(key, io.BytesIO(body))

    document = Document(
        file_name=name,
        original_file_name=name,
        file_type="txt",
        mime_type="text/plain",
        file_size=size,
        storage_key=key,
        file_hash=digest,
        title=title,
        language=language,
        document_type=document_type,
        category_id=category.id if category is not None else None,
        status=DocumentStatus(status),
        created_by=admin.id,
        organization_id=admin.organization_id,
    )
    db.add(document)
    db.flush()
    db.add(DocumentProcessing(document_id=document.id))

    for tag in tags:
        db.add(DocumentTag(document_id=document.id, tag=tag, source=TagSource.AI))

    for index in range(chunks):
        db.add(DocumentChunk(
            organization_id=document.organization_id,
            document_id=document.id,
            chunk_index=index,
            text=f"chunk {index}",
            char_count=7,
            page_number=1,
            vector_point_id=uuid4(),
            metadata_={},
        ))

    db.commit()
    db.refresh(document)
    return document


def audit_actions(db, document_id) -> list[str]:
    rows = db.execute(
        text("SELECT action FROM audit_logs WHERE entity_id = :d ORDER BY created_at"),
        {"d": str(document_id)},
    ).scalars()
    return list(rows)


# --- Auth ---------------------------------------------------------------- #


def test_every_endpoint_requires_authentication(client):
    document_id = uuid4()
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/{document_id}").status_code == 401
    assert client.patch(f"{BASE}/{document_id}", json={"title": "x"}).status_code == 401
    assert client.delete(f"{BASE}/{document_id}").status_code == 401
    assert client.post(f"{BASE}/{document_id}/reprocess").status_code == 401
    assert client.get(f"{BASE}/{document_id}/download").status_code == 401
    assert client.get(f"{BASE}/categories").status_code == 401


# --- List ---------------------------------------------------------------- #


def test_empty_corpus_returns_an_empty_page_not_an_error(client, auth):
    data = client.get(BASE, headers=auth).json()["data"]
    assert data == {"items": [], "page": 1, "page_size": 25, "total": 0, "total_pages": 0}


def test_list_returns_rows_with_counts_and_tags(client, auth, db, storage, admin, categories):
    make_document(db, storage, admin, name="leave.txt", title="Leave Policy",
                  category=categories["hr-policies"], tags=["hr", "leave"], chunks=3)

    data = client.get(BASE, headers=auth).json()["data"]
    assert data["total"] == 1
    row = data["items"][0]
    assert row["file_name"] == "leave.txt"
    assert row["title"] == "Leave Policy"
    assert row["chunk_count"] == 3
    assert row["tags"] == ["hr", "leave"]
    assert row["category"]["slug"] == "hr-policies"


def test_pagination_splits_and_totals_stay_correct(client, auth, db, storage, admin):
    for i in range(7):
        make_document(db, storage, admin, name=f"doc-{i}.txt")

    first = client.get(BASE, params={"page": 1, "page_size": 3}, headers=auth).json()["data"]
    assert len(first["items"]) == 3
    assert first["total"] == 7
    assert first["total_pages"] == 3

    last = client.get(BASE, params={"page": 3, "page_size": 3}, headers=auth).json()["data"]
    assert len(last["items"]) == 1


def test_page_beyond_the_end_is_empty_with_a_correct_total(client, auth, db, storage, admin):
    make_document(db, storage, admin)
    data = client.get(BASE, params={"page": 9, "page_size": 25}, headers=auth).json()["data"]
    assert data["items"] == []
    assert data["total"] == 1


def test_pages_do_not_overlap_when_timestamps_collide(client, auth, db, storage, admin):
    """Rows inserted in one transaction share created_at; the id tie-break is
    what stops a document appearing on two pages or on none."""
    for i in range(6):
        make_document(db, storage, admin, name=f"same-{i}.txt")

    seen = []
    for page in (1, 2, 3):
        data = client.get(BASE, params={"page": page, "page_size": 2},
                          headers=auth).json()["data"]
        seen.extend(item["id"] for item in data["items"])

    assert len(seen) == len(set(seen)) == 6


def test_page_size_is_capped(client, auth, db, storage, admin):
    make_document(db, storage, admin)
    data = client.get(BASE, params={"page_size": 5000}, headers=auth).json()["data"]
    assert data["page_size"] == 100


# --- Search and filters -------------------------------------------------- #


def test_search_matches_the_ai_title(client, auth, db, storage, admin):
    make_document(db, storage, admin, name="a.txt", title="Annual Leave Policy")
    make_document(db, storage, admin, name="b.txt", title="Expense Rules")

    data = client.get(BASE, params={"search": "leave"}, headers=auth).json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Annual Leave Policy"


def test_search_matches_the_uploaded_filename(client, auth, db, storage, admin):
    """The AI renames documents; searching for what was uploaded must still work."""
    make_document(db, storage, admin, name="handbook-2024.txt", title="Something Else")

    data = client.get(BASE, params={"search": "handbook"}, headers=auth).json()["data"]
    assert data["total"] == 1


def test_search_wildcards_are_escaped(client, auth, db, storage, admin):
    """A search for "%" must not match everything."""
    make_document(db, storage, admin, name="a.txt", title="Plain title")
    make_document(db, storage, admin, name="b.txt", title="100% coverage")

    data = client.get(BASE, params={"search": "%"}, headers=auth).json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["title"] == "100% coverage"


def test_filter_by_status(client, auth, db, storage, admin):
    make_document(db, storage, admin, name="ok.txt", status="COMPLETED")
    make_document(db, storage, admin, name="bad.txt", status="FAILED")

    data = client.get(BASE, params={"status": "FAILED"}, headers=auth).json()["data"]
    assert [i["file_name"] for i in data["items"]] == ["bad.txt"]


def test_status_filter_is_repeatable(client, auth, db, storage, admin):
    make_document(db, storage, admin, name="ok.txt", status="COMPLETED")
    make_document(db, storage, admin, name="bad.txt", status="FAILED")
    make_document(db, storage, admin, name="queued.txt", status="QUEUED")

    data = client.get(f"{BASE}?status=FAILED&status=QUEUED", headers=auth).json()["data"]
    assert data["total"] == 2


def test_unknown_status_is_rejected(client, auth):
    r = client.get(BASE, params={"status": "NONSENSE"}, headers=auth)
    assert r.status_code == 422
    assert r.json()["error_code"] == "VALIDATION_ERROR"


def test_filter_by_category(client, auth, db, storage, admin, categories):
    make_document(db, storage, admin, name="hr.txt", category=categories["hr-policies"])
    make_document(db, storage, admin, name="fin.txt", category=categories["finance"])

    data = client.get(BASE, params={"category_id": str(categories["finance"].id)},
                      headers=auth).json()["data"]
    assert [i["file_name"] for i in data["items"]] == ["fin.txt"]


def test_filter_by_document_type_language_and_tag(client, auth, db, storage, admin):
    make_document(db, storage, admin, name="policy.txt", document_type="policy",
                  language="en", tags=["hr"])
    make_document(db, storage, admin, name="invoice.txt", document_type="invoice",
                  language="hi", tags=["finance"])

    by_type = client.get(BASE, params={"document_type": "policy"}, headers=auth).json()["data"]
    assert by_type["total"] == 1

    by_language = client.get(BASE, params={"language": "hi"}, headers=auth).json()["data"]
    assert by_language["items"][0]["file_name"] == "invoice.txt"

    by_tag = client.get(BASE, params={"tag": "finance"}, headers=auth).json()["data"]
    assert by_tag["items"][0]["file_name"] == "invoice.txt"


def test_filters_combine_as_and_not_or(client, auth, db, storage, admin, categories):
    make_document(db, storage, admin, name="match.txt", title="Leave Policy",
                  status="COMPLETED", category=categories["hr-policies"], language="en")
    make_document(db, storage, admin, name="wrong-status.txt", title="Leave Policy",
                  status="FAILED", category=categories["hr-policies"], language="en")
    make_document(db, storage, admin, name="wrong-category.txt", title="Leave Policy",
                  status="COMPLETED", category=categories["finance"], language="en")

    data = client.get(
        BASE,
        params={"search": "leave", "status": "COMPLETED",
                "category_id": str(categories["hr-policies"].id), "language": "en"},
        headers=auth,
    ).json()["data"]
    assert [i["file_name"] for i in data["items"]] == ["match.txt"]


def test_a_filter_matching_nothing_is_an_empty_list_not_an_error(client, auth, db, storage, admin):
    make_document(db, storage, admin)
    r = client.get(BASE, params={"search": "nothing-matches-this"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["data"]["items"] == []


# --- Sorting ------------------------------------------------------------- #


def test_sort_by_file_size(client, auth, db, storage, admin):
    make_document(db, storage, admin, name="small.txt", body=b"a")
    make_document(db, storage, admin, name="large.txt", body=b"a" * 5000)

    asc = client.get(BASE, params={"sort": "file_size", "order": "asc"},
                     headers=auth).json()["data"]
    assert [i["file_name"] for i in asc["items"]] == ["small.txt", "large.txt"]

    desc = client.get(BASE, params={"sort": "file_size", "order": "desc"},
                      headers=auth).json()["data"]
    assert [i["file_name"] for i in desc["items"]] == ["large.txt", "small.txt"]


def test_sort_by_file_name(client, auth, db, storage, admin):
    make_document(db, storage, admin, name="zebra.txt")
    make_document(db, storage, admin, name="alpha.txt")

    data = client.get(BASE, params={"sort": "file_name", "order": "asc"},
                      headers=auth).json()["data"]
    assert [i["file_name"] for i in data["items"]] == ["alpha.txt", "zebra.txt"]


@pytest.mark.parametrize("column", ["password_hash", "id; DROP TABLE documents", "created_by"])
def test_sort_whitelist_rejects_anything_else(client, auth, column):
    """A column name from a query string is an injection vector — parameters
    bind values, never identifiers."""
    r = client.get(BASE, params={"sort": column}, headers=auth)
    assert r.status_code == 422
    assert "created_at" in r.json()["details"]["allowed"]


def test_unknown_order_is_rejected(client, auth):
    assert client.get(BASE, params={"order": "sideways"}, headers=auth).status_code == 422


# --- Detail -------------------------------------------------------------- #


def test_detail_includes_processing_and_chunk_count(client, auth, db, storage, admin, categories):
    document = make_document(db, storage, admin, title="Leave Policy",
                             category=categories["hr-policies"], tags=["hr"], chunks=4)

    data = client.get(f"{BASE}/{document.id}", headers=auth).json()["data"]
    assert data["chunk_count"] == 4
    assert data["tags"] == ["hr"]
    assert data["original_file_name"] == "leave.txt"
    assert data["processing"] is not None


def test_detail_of_an_unknown_id_is_404(client, auth):
    r = client.get(f"{BASE}/{uuid4()}", headers=auth)
    assert r.status_code == 404
    assert r.json()["error_code"] == "DOCUMENT_NOT_FOUND"


def test_categories_endpoint_excludes_inactive(client, auth, categories):
    data = client.get(f"{BASE}/categories", headers=auth).json()["data"]
    slugs = [c["slug"] for c in data["categories"]]
    assert "hr-policies" in slugs
    assert "retired" not in slugs
    assert "COMPLETED" in data["statuses"]


# --- Edit ---------------------------------------------------------------- #


def test_metadata_edit_persists(client, auth, db, storage, admin, categories):
    document = make_document(db, storage, admin, title="Old title")

    r = client.patch(f"{BASE}/{document.id}", headers=auth, json={
        "title": "New title",
        "description": "A clearer description.",
        "category_id": str(categories["finance"].id),
        "tags": ["Finance", "finance", "  Budget  "],
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["title"] == "New title"
    assert data["category"]["slug"] == "finance"
    # Normalised, deduplicated, order preserved.
    assert data["tags"] == ["budget", "finance"]


def test_edit_writes_an_audit_row(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    client.patch(f"{BASE}/{document.id}", headers=auth, json={"title": "Renamed"})
    assert "document.updated" in audit_actions(db, document.id)


def test_edit_during_processing_is_rejected(client, auth, db, storage, admin):
    """The pipeline would overwrite the edit moments later."""
    document = make_document(db, storage, admin, status="EMBEDDING")

    r = client.patch(f"{BASE}/{document.id}", headers=auth, json={"title": "Renamed"})
    assert r.status_code == 409
    assert r.json()["error_code"] == "DOCUMENT_NOT_EDITABLE"


def test_edit_of_a_queued_document_is_rejected(client, auth, db, storage, admin):
    """QUEUED counts as moving: a worker can claim it a millisecond later."""
    document = make_document(db, storage, admin, status="QUEUED")
    r = client.patch(f"{BASE}/{document.id}", headers=auth, json={"title": "Renamed"})
    assert r.status_code == 409


def test_edit_cannot_reach_a_deleted_document(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    client.delete(f"{BASE}/{document.id}", headers=auth)

    r = client.patch(f"{BASE}/{document.id}", headers=auth, json={"title": "Renamed"})
    assert r.status_code == 404


def test_category_can_be_cleared_explicitly(client, auth, db, storage, admin, categories):
    document = make_document(db, storage, admin, category=categories["hr-policies"])

    r = client.patch(f"{BASE}/{document.id}", headers=auth, json={"category_id": None})
    assert r.status_code == 200
    assert r.json()["data"]["category"] is None


def test_omitting_category_leaves_it_alone(client, auth, db, storage, admin, categories):
    """An omitted field and an explicit null must not mean the same thing."""
    document = make_document(db, storage, admin, category=categories["hr-policies"])

    r = client.patch(f"{BASE}/{document.id}", headers=auth, json={"title": "Renamed"})
    assert r.json()["data"]["category"]["slug"] == "hr-policies"


def test_an_inactive_category_is_rejected(client, auth, db, storage, admin, categories):
    document = make_document(db, storage, admin)
    r = client.patch(f"{BASE}/{document.id}", headers=auth,
                     json={"category_id": str(categories["retired"].id)})
    assert r.status_code == 422


def test_an_unknown_category_is_rejected(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    r = client.patch(f"{BASE}/{document.id}", headers=auth,
                     json={"category_id": str(uuid4())})
    assert r.status_code == 422


def test_editing_cannot_change_the_text(client, auth, db, storage, admin):
    """There is no field that can invalidate the vectors (§29)."""
    from app.modules.documents.schemas.management import DocumentUpdateRequest

    assert "text" not in DocumentUpdateRequest.model_fields
    assert "content" not in DocumentUpdateRequest.model_fields

    document = make_document(db, storage, admin, chunks=2)
    client.patch(f"{BASE}/{document.id}", headers=auth,
                 json={"title": "Renamed", "text": "tampered"})

    rows = db.execute(
        text("SELECT text FROM document_chunks WHERE document_id = :d ORDER BY chunk_index"),
        {"d": str(document.id)},
    ).scalars().all()
    assert rows == ["chunk 0", "chunk 1"]


# --- Delete -------------------------------------------------------------- #


def test_delete_is_soft_and_hides_the_row(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)

    r = client.delete(f"{BASE}/{document.id}", headers=auth)
    assert r.status_code == 200

    assert client.get(BASE, headers=auth).json()["data"]["total"] == 0

    # The row survives so citations still resolve.
    row = db.execute(
        text("SELECT status, deleted_at FROM documents WHERE id = :d"),
        {"d": str(document.id)},
    ).one()
    assert row.status == "DELETED"
    assert row.deleted_at is not None


def test_deleted_rows_can_be_listed_explicitly(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    client.delete(f"{BASE}/{document.id}", headers=auth)

    data = client.get(BASE, params={"include_deleted": "true"}, headers=auth).json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["deleted_at"] is not None


def test_deleting_twice_is_a_conflict(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    client.delete(f"{BASE}/{document.id}", headers=auth)

    r = client.delete(f"{BASE}/{document.id}", headers=auth)
    assert r.status_code == 409
    assert r.json()["error_code"] == "DOCUMENT_ALREADY_DELETED"


def test_delete_writes_an_audit_row(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    client.delete(f"{BASE}/{document.id}", headers=auth)
    assert "document.deleted" in audit_actions(db, document.id)


def test_delete_while_processing_is_allowed(client, auth, db, storage, admin):
    """The task detects deleted_at, aborts, and indexes nothing."""
    document = make_document(db, storage, admin, status="EMBEDDING")
    assert client.delete(f"{BASE}/{document.id}", headers=auth).status_code == 200


def test_delete_keeps_the_source_file_by_default(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    key = document.storage_key
    client.delete(f"{BASE}/{document.id}", headers=auth)
    assert storage.exists(key), "a soft delete should stay recoverable"


# --- Reprocess ----------------------------------------------------------- #


@needs_qdrant
def test_reprocess_requeues_and_clears_old_chunks(client, auth, db, storage, admin):
    document = make_document(db, storage, admin, status="FAILED", chunks=3)

    r = client.post(f"{BASE}/{document.id}/reprocess", headers=auth)
    assert r.status_code == 202, r.text
    data = r.json()["data"]
    assert data["status"] == "QUEUED"
    assert data["removed_chunks"] == 3

    remaining = db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id = :d"),
        {"d": str(document.id)},
    ).scalar_one()
    assert remaining == 0


@needs_qdrant
def test_reprocess_clears_the_previous_error(client, auth, db, storage, admin):
    """A stale error on a requeued document reads as a fresh failure."""
    document = make_document(db, storage, admin, status="FAILED")
    db.execute(
        text("""UPDATE document_processing
                SET error_code = 'OCR_FAILED', processing_error = 'old', retry_count = 3
                WHERE document_id = :d"""),
        {"d": str(document.id)},
    )
    db.commit()

    client.post(f"{BASE}/{document.id}/reprocess", headers=auth)

    row = db.execute(
        text("""SELECT error_code, processing_error, retry_count
                FROM document_processing WHERE document_id = :d"""),
        {"d": str(document.id)},
    ).one()
    assert row.error_code is None
    assert row.processing_error is None
    assert row.retry_count == 0


@needs_qdrant
def test_reprocess_clears_the_content_hash_so_the_duplicate_check_reruns(
    client, auth, db, storage, admin
):
    """The original may itself have been deleted since."""
    document = make_document(db, storage, admin, status="DUPLICATE")
    db.execute(text("UPDATE documents SET content_hash = :h WHERE id = :d"),
               {"h": "0" * 64, "d": str(document.id)})
    db.commit()

    client.post(f"{BASE}/{document.id}/reprocess", headers=auth)

    row = db.execute(
        text("SELECT content_hash, duplicate_of_document_id FROM documents WHERE id = :d"),
        {"d": str(document.id)},
    ).one()
    assert row.content_hash is None
    assert row.duplicate_of_document_id is None


@needs_qdrant
def test_reprocess_writes_an_audit_row(client, auth, db, storage, admin):
    document = make_document(db, storage, admin, status="COMPLETED")
    client.post(f"{BASE}/{document.id}/reprocess", headers=auth)
    assert "document.reprocessed" in audit_actions(db, document.id)


@pytest.mark.parametrize("status", ["QUEUED", "PROCESSING", "EMBEDDING"])
def test_reprocessing_a_moving_document_is_rejected(client, auth, db, storage, admin, status):
    document = make_document(db, storage, admin, status=status)
    r = client.post(f"{BASE}/{document.id}/reprocess", headers=auth)
    assert r.status_code == 409
    assert r.json()["error_code"] == "DOCUMENT_NOT_REPROCESSABLE"


def test_reprocessing_without_the_source_file_is_rejected(client, auth, db, storage, admin):
    document = make_document(db, storage, admin, status="FAILED", with_blob=False)
    r = client.post(f"{BASE}/{document.id}/reprocess", headers=auth)
    assert r.status_code == 409
    assert r.json()["error_code"] == "STORAGE_FILE_MISSING"


# --- Download ------------------------------------------------------------ #


def test_download_streams_the_original_as_an_attachment(client, auth, db, storage, admin):
    document = make_document(db, storage, admin, name="leave.txt", body=TXT)

    r = client.get(f"{BASE}/{document.id}/download", headers=auth)
    assert r.status_code == 200
    assert r.content == TXT
    # attachment, not inline: an uploaded HTML file must not execute here.
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "leave.txt" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_download_writes_an_audit_row(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    client.get(f"{BASE}/{document.id}/download", headers=auth)
    assert "document.downloaded" in audit_actions(db, document.id)


def test_download_with_a_missing_blob_reports_it(client, auth, db, storage, admin):
    document = make_document(db, storage, admin, with_blob=False)
    r = client.get(f"{BASE}/{document.id}/download", headers=auth)
    assert r.status_code == 500
    assert r.json()["error_code"] == "STORAGE_FILE_MISSING"


def test_download_of_a_deleted_document_is_404(client, auth, db, storage, admin):
    document = make_document(db, storage, admin)
    client.delete(f"{BASE}/{document.id}", headers=auth)
    assert client.get(f"{BASE}/{document.id}/download", headers=auth).status_code == 404


# --- Vectors (Qdrant) ---------------------------------------------------- #


@pytest.fixture()
def indexed(db, storage, admin):
    """A document with real points in Qdrant, without running the pipeline."""
    from app.vector import repository as vector_repo
    from app.vector.client import ensure_collection

    document = make_document(db, storage, admin, title="Leave Policy", chunks=0)
    ensure_collection(document.organization_id)

    rows = []
    from app.modules.documents.models import DocumentChunk
    from app.vector.client import point_id_for

    for index in range(3):
        row = DocumentChunk(
            organization_id=document.organization_id,
            document_id=document.id, chunk_index=index, text=f"chunk {index}",
            char_count=7, page_number=1,
            vector_point_id=UUID(point_id_for(document.id, index)), metadata_={},
        )
        db.add(row)
        rows.append(row)
    db.commit()

    payloads = [
        vector_repo.ChunkPayload(
            organization_id=str(document.organization_id),
            is_public=False,
            document_id=str(document.id), chunk_id=str(row.id), chunk_index=row.chunk_index,
            text=row.text, document_name=document.original_file_name,
            document_type=None, category_slug=None, language="en", tags=["old"],
            page_number=1, section=None, status="COMPLETED",
            created_at=document.created_at.isoformat(),
        )
        for row in rows
    ]
    vector_repo.upsert_chunks(document.organization_id, document.id, [[0.01] * 768 for _ in rows], payloads)
    yield document

    try:
        vector_repo.delete_document_vectors(document.organization_id, document.id)
    except Exception:
        pass


@needs_qdrant
def test_delete_removes_the_vectors(client, auth, indexed):
    from app.vector.repository import count_document_vectors

    assert count_document_vectors(indexed.organization_id, indexed.id) == 3

    r = client.delete(f"{BASE}/{indexed.id}", headers=auth)
    assert r.json()["data"]["vectors_removed"] is True
    assert count_document_vectors(indexed.organization_id, indexed.id) == 0


@needs_qdrant
def test_metadata_edit_updates_the_qdrant_payload(client, auth, indexed, categories):
    """Skip this and search still works, but citations render the old name."""
    from app.vector.client import collection_for, get_client, point_id_for
    from config.settings import settings

    r = client.patch(f"{BASE}/{indexed.id}", headers=auth, json={
        "document_type": "policy",
        "category_id": str(categories["hr-policies"].id),
        "tags": ["hr", "leave"],
    })
    assert r.status_code == 200, r.text

    points = get_client().retrieve(
        collection_name=collection_for(indexed.organization_id),
        ids=[point_id_for(indexed.id, i) for i in range(3)],
        with_payload=True,
    )
    assert len(points) == 3
    for point in points:
        assert point.payload["category_slug"] == "hr-policies"
        assert point.payload["document_type"] == "policy"
        assert point.payload["tags"] == ["hr", "leave"]


@needs_qdrant
def test_metadata_edit_does_not_touch_the_vectors(client, auth, indexed):
    """The text has not changed, so re-embedding would produce identical
    numbers at the cost of a CPU-bound minute (§29)."""
    from app.vector.client import collection_for, get_client, point_id_for
    from config.settings import settings

    def vectors():
        points = get_client().retrieve(
            collection_name=collection_for(indexed.organization_id),
            ids=[point_id_for(indexed.id, i) for i in range(3)],
            with_vectors=True,
        )
        return {p.id: p.vector for p in points}

    before = vectors()
    client.patch(f"{BASE}/{indexed.id}", headers=auth, json={"title": "Renamed entirely"})
    assert vectors() == before


@needs_qdrant
def test_reprocess_removes_old_vectors_before_requeueing(client, auth, indexed):
    """Deleting first is what stops a shortened document leaving orphans (§29)."""
    from app.vector.repository import count_document_vectors

    r = client.post(f"{BASE}/{indexed.id}/reprocess", headers=auth)
    assert r.status_code == 202
    assert count_document_vectors(indexed.organization_id, indexed.id) == 0


# --- Date range ---------------------------------------------------------- #


def test_created_to_covers_the_whole_day(client, auth, db, storage, admin):
    """A range of "today to today" must not come back empty."""
    document = make_document(db, storage, admin)
    today = db.execute(text("SELECT (now() AT TIME ZONE 'UTC')::date")).scalar_one()

    data = client.get(
        BASE,
        params={"created_from": str(today), "created_to": str(today)},
        headers=auth,
    ).json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(document.id)


def test_created_from_excludes_earlier_documents(client, auth, db, storage, admin):
    old = make_document(db, storage, admin, name="old.txt")
    db.execute(text("UPDATE documents SET created_at = now() - interval '30 days' WHERE id = :d"),
               {"d": str(old.id)})
    db.commit()
    make_document(db, storage, admin, name="new.txt")

    tomorrow = db.execute(
        text("SELECT ((now() AT TIME ZONE 'UTC')::date - 1)")
    ).scalar_one()
    data = client.get(BASE, params={"created_from": str(tomorrow)}, headers=auth).json()["data"]
    assert [i["file_name"] for i in data["items"]] == ["new.txt"]
