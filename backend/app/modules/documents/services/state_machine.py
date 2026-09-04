"""Document state machine (spec §21, docs/celery/state-machine.md).

Transitions go through one function. Nothing assigns ``status`` directly.

An illegal transition raises rather than silently correcting: a bug that would
quietly resurrect a deleted document fails in a test instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import IllegalTransition
from app.core.logging import get_logger
from app.modules.documents.models import (
    TERMINAL_STATUSES,
    Document,
    DocumentProcessing,
    DocumentStatus,
)

logger = get_logger(__name__)

S = DocumentStatus

# Ordered pipeline stages, used for progress and for the default forward path.
PIPELINE_ORDER: tuple[DocumentStatus, ...] = (
    S.PROCESSING,
    S.EXTRACTING,
    S.OCR,
    S.CLASSIFYING,
    S.CHUNKING,
    S.EMBEDDING,
    S.INDEXING,
)

PROGRESS: dict[DocumentStatus, int] = {
    S.UPLOADING: 0,
    S.VALIDATING: 0,
    S.DUPLICATE_CHECK: 0,
    S.QUEUED: 0,
    S.PROCESSING: 10,
    S.EXTRACTING: 25,
    S.OCR: 40,
    S.CLASSIFYING: 55,
    S.CHUNKING: 70,
    S.EMBEDDING: 80,
    S.INDEXING: 90,
    S.COMPLETED: 100,
    S.FAILED: 100,
    S.DUPLICATE: 100,
    S.DELETED: 100,
}


def _forward(*extra: DocumentStatus) -> set[DocumentStatus]:
    """Every active state may also fail or be deleted."""
    return {*extra, S.FAILED, S.DELETED}


ALLOWED: dict[DocumentStatus, set[DocumentStatus]] = {
    S.UPLOADING: _forward(S.VALIDATING, S.QUEUED),
    S.VALIDATING: _forward(S.DUPLICATE_CHECK, S.QUEUED, S.DUPLICATE),
    S.DUPLICATE_CHECK: _forward(S.QUEUED, S.DUPLICATE),
    S.QUEUED: _forward(S.PROCESSING),
    S.PROCESSING: _forward(S.EXTRACTING),
    S.EXTRACTING: _forward(S.OCR, S.CLASSIFYING, S.DUPLICATE),
    S.OCR: _forward(S.CLASSIFYING, S.DUPLICATE),
    S.CLASSIFYING: _forward(S.CHUNKING),
    S.CHUNKING: _forward(S.EMBEDDING),
    S.EMBEDDING: _forward(S.INDEXING),
    S.INDEXING: _forward(S.COMPLETED),
    # Terminal states re-enter the pipeline only by explicit admin action.
    S.COMPLETED: {S.QUEUED, S.DELETED},
    S.FAILED: {S.QUEUED, S.DELETED},
    S.DUPLICATE: {S.QUEUED, S.DELETED},
    S.DELETED: set(),
}


def can_transition(current: DocumentStatus, target: DocumentStatus) -> bool:
    return target in ALLOWED.get(current, set())


def transition(
    db: Session,
    document: Document,
    target: DocumentStatus,
    *,
    stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    commit: bool = True,
) -> None:
    """Move a document to ``target``, recording the fine-grained stage.

    Committed immediately at every step: the status endpoint is only useful if
    it reflects reality within a second or two.
    """
    current = document.status

    if current == target and stage is not None:
        # Re-entering the same coarse state with a new fine stage is fine.
        pass
    elif not can_transition(current, target):
        raise IllegalTransition(
            f"Illegal transition {current} -> {target}",
            details={"document_id": str(document.id), "from": str(current), "to": str(target)},
        )

    document.status = target

    processing: DocumentProcessing | None = document.processing
    if processing is not None:
        processing.current_stage = stage or target.value.lower()

        now = datetime.now(timezone.utc)
        if target is S.PROCESSING and processing.processing_started_at is None:
            processing.processing_started_at = now

        if target in TERMINAL_STATUSES:
            processing.processing_completed_at = now
            if processing.processing_started_at is not None:
                delta = now - processing.processing_started_at
                processing.duration_ms = int(delta.total_seconds() * 1000)

        if error_code is not None:
            processing.error_code = error_code
        if error_message is not None:
            # Operator-facing only. Never a raw stack trace, never PII.
            processing.processing_error = error_message[:2000]

    if commit:
        db.commit()

    logger.info(
        "Status transition",
        extra={
            "document_id": str(document.id),
            "from": str(current),
            "to": str(target),
            "stage": stage,
            "error_code": error_code,
        },
    )


def progress_percent(status: DocumentStatus) -> int:
    return PROGRESS.get(status, 0)


def is_terminal(status: DocumentStatus) -> bool:
    return status in TERMINAL_STATUSES
