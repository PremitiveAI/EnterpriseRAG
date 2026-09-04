"""Upload endpoint against a real database.

Uses the test database from conftest (never the development one) and rolls the
schema forward with the same Alembic migrations the application uses.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import make_tenant, otp_login, reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

PDF = b"%PDF-1.7\n" + b"0" * 512
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 512
TXT = b"Employee handbook. Annual leave is 24 days.\n"


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
    """Every test starts from an empty corpus."""
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


def _part(name: str, data: bytes, mime: str = "application/octet-stream"):
    return ("files", (name, io.BytesIO(data), mime))


def upload(client, auth, *parts):
    return client.post("/api/v1/admin/documents/upload", files=list(parts), headers=auth)


# --- Auth --------------------------------------------------------------- #


def test_upload_requires_authentication(client):
    r = client.post("/api/v1/admin/documents/upload", files=[_part("a.pdf", PDF)])
    assert r.status_code == 401


# --- Happy path --------------------------------------------------------- #


def test_single_valid_file_is_queued(client, auth):
    r = upload(client, auth, _part("report.pdf", PDF))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["accepted"] == 1
    result = data["results"][0]
    assert result["status"] == "QUEUED"
    assert result["document_id"]


def test_multiple_files_all_queued(client, auth):
    r = upload(client, auth,
               _part("a.pdf", PDF), _part("b.png", PNG), _part("c.txt", TXT))
    assert r.json()["data"]["accepted"] == 3


def test_client_supplied_mime_is_ignored(client, auth):
    """A PDF declared as text/plain is still stored as a PDF (§17)."""
    r = upload(client, auth, ("files", ("report.pdf", io.BytesIO(PDF), "text/plain")))
    assert r.json()["data"]["results"][0]["status"] == "QUEUED"


# --- Rejections --------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,data,code",
    [
        ("diagram.bmp", PDF, "INVALID_FILE_TYPE"),
        ("legacy.doc", PDF, "LEGACY_FORMAT_UNSUPPORTED"),
        ("deck.ppt", PDF, "LEGACY_FORMAT_UNSUPPORTED"),
        ("empty.pdf", b"", "FILE_EMPTY"),
        ("renamed.pdf", PNG, "FILE_SIGNATURE_MISMATCH"),
        ("README", PDF, "INVALID_FILE_TYPE"),
    ],
)
def test_invalid_files_rejected_with_specific_codes(client, auth, name, data, code):
    r = upload(client, auth, _part(name, data))
    result = r.json()["data"]["results"][0]
    assert result["status"] == "REJECTED"
    assert result["error_code"] == code


def test_oversized_file_rejected(client, auth):
    big = b"%PDF-1.7\n" + b"0" * (21 * 1024 * 1024)
    result = upload(client, auth, _part("big.pdf", big)).json()["data"]["results"][0]
    assert result["error_code"] == "FILE_TOO_LARGE"


def test_image_uses_the_smaller_limit(client, auth):
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024)
    result = upload(client, auth, _part("big.png", big)).json()["data"]["results"][0]
    assert result["error_code"] == "FILE_TOO_LARGE"


def test_traversal_filename_is_neutralised(client, auth, db):
    upload(client, auth, _part("../../../etc/passwd.pdf", PDF))
    stored = db.execute(text("SELECT file_name, storage_key FROM documents")).one()
    assert "/" not in stored.file_name and "\\" not in stored.file_name
    assert ".." not in stored.storage_key


# --- Duplicates --------------------------------------------------------- #


def test_exact_duplicate_across_requests(client, auth):
    upload(client, auth, _part("first.pdf", PDF))
    result = upload(client, auth, _part("second.pdf", PDF)).json()["data"]["results"][0]

    assert result["status"] == "DUPLICATE"
    assert result["error_code"] == "DUPLICATE_DOCUMENT"
    # The original must be named, so the admin sees what it collided with.
    assert result["duplicate_of"]["file_name"] == "first.pdf"


def test_duplicate_within_one_batch(client, auth):
    data = upload(client, auth, _part("a.pdf", PDF), _part("a-copy.pdf", PDF)).json()["data"]
    assert data["accepted"] == 1
    assert data["duplicates"] == 1
    assert data["results"][1]["error_code"] == "DUPLICATE_IN_BATCH"


def test_reupload_after_soft_delete_is_accepted(client, auth, db):
    """Deletion is not a permanent ban (ADR-005)."""
    upload(client, auth, _part("first.pdf", PDF))
    db.execute(text("UPDATE documents SET deleted_at = now(), status = 'DELETED'"))
    db.commit()

    result = upload(client, auth, _part("again.pdf", PDF)).json()["data"]["results"][0]
    assert result["status"] == "QUEUED"


# --- Partial success (§19) ---------------------------------------------- #


def test_batch_is_partially_successful(client, auth):
    body = upload(
        client, auth,
        _part("good1.pdf", PDF),
        _part("good2.txt", TXT),
        _part("dupe.pdf", PDF),
        _part("bad.bmp", PDF),
        _part("legacy.doc", PDF),
    ).json()

    # One invalid file never fails the batch.
    assert body["success"] is True
    data = body["data"]
    assert (data["accepted"], data["duplicates"], data["rejected"]) == (2, 1, 2)
    assert len(data["results"]) == 5


def test_too_many_files_is_a_request_level_error(client, auth):
    parts = [_part(f"f{i}.pdf", PDF + str(i).encode()) for i in range(25)]
    r = upload(client, auth, *parts)
    assert r.status_code == 400
    assert r.json()["error_code"] == "TOO_MANY_FILES"


# --- Persistence -------------------------------------------------------- #


def test_row_and_processing_row_created(client, auth, db):
    upload(client, auth, _part("report.pdf", PDF))
    row = db.execute(
        text("""SELECT d.status, d.file_hash, d.mime_type, d.storage_key, p.id AS proc
                FROM documents d JOIN document_processing p ON p.document_id = d.id""")
    ).one()
    assert row.status == "QUEUED"
    assert len(row.file_hash) == 64
    assert row.mime_type == "application/pdf"
    assert row.proc is not None


def test_upload_is_audited_without_file_contents(client, auth, db):
    upload(client, auth, _part("report.pdf", PDF))
    row = db.execute(
        text("SELECT action, metadata FROM audit_logs WHERE action = 'document.upload'")
    ).one()
    assert row.action == "document.upload"
    assert row.metadata["file_name"] == "report.pdf"
    # Filenames and sizes only — never contents (§39).
    assert "content" not in row.metadata and "text" not in row.metadata


def test_stored_file_exists_on_disk(client, auth, db):
    upload(client, auth, _part("report.pdf", PDF))
    key = db.execute(text("SELECT storage_key FROM documents")).scalar_one()

    from app.storage.local import get_storage

    assert get_storage().exists(key)
    get_storage().delete(key)


def test_rejected_file_writes_nothing(client, auth, db):
    upload(client, auth, _part("bad.bmp", PDF))
    assert db.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0


# --- Config ------------------------------------------------------------- #


def test_upload_limits_endpoint_matches_server_rules(client, auth):
    data = client.get("/api/v1/config/upload-limits", headers=auth).json()["data"]
    assert data["max_document_size_mb"] == 20
    assert data["max_image_size_mb"] == 2
    assert "pdf" in data["document_extensions"]
    assert "doc" in data["legacy_extensions"]


# --- Worker wiring ------------------------------------------------------ #


def test_every_enqueued_task_is_registered_with_the_worker():
    """Guards against NotRegistered.

    A task the API enqueues but the worker never imports fails silently from the
    caller's point of view: the upload succeeds, and the document sits at QUEUED
    forever. This asserts the worker knows every task name the app dispatches.
    """
    from app.workers.celery_app import celery_app

    # conf.imports is processed when a worker boots, not on plain import — so
    # do exactly what the worker does, then check what it ended up knowing.
    celery_app.loader.import_default_modules()

    registered = set(celery_app.tasks)
    for name in ("documents.process", "system.health"):
        assert name in registered, f"{name} is enqueued but not in celery_app.conf.imports"
