"""Document processing task (spec §22, §23).

Retry policy is explicit per stage. ``autoretry_for`` is deliberately empty:
a blanket retry would re-run a corrupt PDF three times and burn 90 minutes of a
single-slot worker to reach the same conclusion (docs/celery/tasks.md).
"""

from __future__ import annotations

from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded

from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.logging import get_logger
from app.modules.documents.models import Document, DocumentStatus
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.services.pipeline_service import (
    ProcessingPipeline,
    RetryableStageError,
)
from app.modules.documents.services.state_machine import transition
from app.storage.local import get_storage
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

MAX_RETRIES = 3


@celery_app.task(
    bind=True,
    name="documents.process",
    max_retries=MAX_RETRIES,
    soft_time_limit=1800,  # raises inside the task, so it can record why
    time_limit=2100,       # hard kill for a task that ignores the soft signal
    autoretry_for=(),
)
def process_document(self, document_id: str) -> dict[str, object]:
    db = SessionLocal()
    try:
        pipeline = ProcessingPipeline(db, get_storage())
        result = pipeline.run(UUID(document_id))

        return {
            "document_id": result.document_id,
            "status": result.status,
            "chunks": result.chunks,
            "vectors": result.vectors,
            "degraded": result.degraded,
            "duplicate_of": result.duplicate_of,
            "error_code": result.error_code,
        }

    except RetryableStageError as exc:
        # Transient: Qdrant restarting, memory pressure, a rate limit.
        # 10s, 20s, 40s — long enough for a restart, short enough that a real
        # outage surfaces within a minute.
        retries = self.request.retries
        if retries < MAX_RETRIES:
            _record_retry(db, document_id, retries + 1)
            raise self.retry(exc=exc, countdown=10 * (2**retries)) from exc

        _mark_failed(db, document_id, ErrorCode.RETRY_LIMIT_EXCEEDED,
                     f"Failed after {MAX_RETRIES} retries: {exc.error_code}")
        return {"document_id": document_id, "status": "FAILED",
                "error_code": str(ErrorCode.RETRY_LIMIT_EXCEEDED)}

    except SoftTimeLimitExceeded:
        # Without the soft limit the task would be killed outright and the
        # document would show its last stage forever.
        _mark_failed(db, document_id, ErrorCode.PROCESSING_FAILED,
                     "Processing exceeded the time limit.")
        return {"document_id": document_id, "status": "FAILED",
                "error_code": str(ErrorCode.PROCESSING_FAILED)}

    except Exception as exc:
        logger.exception("Unhandled processing error", extra={"document_id": document_id})
        _mark_failed(db, document_id, ErrorCode.PROCESSING_FAILED,
                     f"Unexpected error: {type(exc).__name__}")
        return {"document_id": document_id, "status": "FAILED",
                "error_code": str(ErrorCode.PROCESSING_FAILED)}
    finally:
        db.close()


def _record_retry(db, document_id: str, attempt: int) -> None:
    try:
        # See _mark_failed: the session may be poisoned by the failure that
        # brought us here, and every read on it would raise until it is cleared.
        db.rollback()
        # Same reasoning as the pipeline: the task has an id, not a tenant.
        document = db.get(Document, UUID(document_id))
        if document is not None and document.processing is not None:
            document.processing.retry_count = attempt
            db.commit()
    except Exception:  # pragma: no cover - bookkeeping must not mask the retry
        db.rollback()


def _mark_failed(db, document_id: str, code: ErrorCode, message: str) -> None:
    try:
        # FIRST, before touching the session.
        #
        # We are here because something raised, and when that something was a
        # failed flush the session is left in a rolled-back transaction that
        # refuses every subsequent operation with PendingRollbackError. Without
        # this line the recovery path raises too, the document is never marked
        # FAILED, and it sits at PROCESSING forever showing a stage it left long
        # ago - the log says "Could not mark document failed" and that is all
        # anyone ever learns about it.
        db.rollback()

        # Same reasoning as the pipeline: the task has an id, not a tenant.
        document = db.get(Document, UUID(document_id))
        if document is None:
            return
        if document.status in {DocumentStatus.COMPLETED, DocumentStatus.DELETED}:
            return
        transition(db, document, DocumentStatus.FAILED, stage="failed",
                   error_code=str(code), error_message=message)
    except Exception:  # pragma: no cover
        db.rollback()
        logger.exception("Could not mark document failed", extra={"document_id": document_id})
