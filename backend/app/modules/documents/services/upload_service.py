"""Upload business logic (spec §16-§20, docs/features/document-upload.md).

Everything here is cheap: validation, hashing and two index lookups. No AI
call, no parsing and no OCR happens on the request path — that is the whole
point of §20, and the reason content-level duplicate detection is deferred to
the Celery task (ADR-005).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import BinaryIO, Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.logging import get_logger
from app.modules.auth.models import AuditAction
from app.modules.auth.repositories.user_repository import AuditRepository
from app.modules.documents.models import DocumentStatus
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.schemas.upload import (
    DuplicateRef,
    FileResult,
    UploadOutcome,
    UploadSummary,
)
from app.storage.base import StorageService
from app.utils.file_validation import (
    SIGNATURE_PEEK_BYTES,
    MIME_TYPES,
    ValidationFailure,
    validate_content,
    validate_metadata,
)
from app.utils.hashing import sha256_stream

logger = get_logger(__name__)


class IncomingFile(Protocol):
    """The subset of Starlette's UploadFile this service needs."""

    filename: str | None
    file: BinaryIO


class UploadService:
    def __init__(self, db: Session, storage: StorageService, organization_id: UUID) -> None:
        self.db = db
        self.storage = storage
        self.organization_id = organization_id
        self.documents = DocumentRepository(db)
        self.audit = AuditRepository(db)

    # ------------------------------------------------------------------ #

    def upload_batch(
        self,
        files: list[IncomingFile],
        *,
        user_id: UUID,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> UploadSummary:
        results: list[FileResult] = []
        # Maps a hash seen earlier in THIS request to the name it arrived under,
        # so a within-batch collision can name its counterpart.
        seen_hashes: dict[str, str] = {}

        for incoming in files:
            result = self._process_one(
                incoming,
                seen_hashes=seen_hashes,
                user_id=user_id,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            results.append(result)

        return UploadSummary(
            accepted=sum(r.status is UploadOutcome.QUEUED for r in results),
            duplicates=sum(r.status is UploadOutcome.DUPLICATE for r in results),
            rejected=sum(r.status is UploadOutcome.REJECTED for r in results),
            results=results,
        )

    # ------------------------------------------------------------------ #

    def _process_one(
        self,
        incoming: IncomingFile,
        *,
        seen_hashes: dict[str, str],
        user_id: UUID,
        request_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> FileResult:
        raw_name = incoming.filename or ""

        # 1-2. Filename and extension.
        meta = validate_metadata(raw_name)
        if isinstance(meta, ValidationFailure):
            return _rejected(raw_name, meta)
        safe_name, extension = meta

        stream = incoming.file

        # 3-4. Signature, from the bytes. The client's Content-Type is ignored.
        stream.seek(0)
        head = stream.read(SIGNATURE_PEEK_BYTES)
        stream.seek(0)

        # 5-7. Hash and size in one streamed pass.
        file_hash, size_bytes = sha256_stream(stream)
        stream.seek(0)

        failure = validate_content(extension, head, size_bytes)
        if failure is not None:
            return _rejected(safe_name, failure)

        # 8. Within this request.
        if (twin := seen_hashes.get(file_hash)) is not None:
            return FileResult(
                file_name=safe_name,
                status=UploadOutcome.DUPLICATE,
                error_code=str(ErrorCode.DUPLICATE_IN_BATCH),
                message=f"Identical to {twin} in this upload.",
            )

        # 9. Against stored documents. Soft-deleted rows are excluded, so a
        #    re-upload after deletion is accepted.
        if (existing := self.documents.find_by_file_hash(self.organization_id, file_hash)) is not None:
            return FileResult(
                file_name=safe_name,
                status=UploadOutcome.DUPLICATE,
                error_code=str(ErrorCode.DUPLICATE_DOCUMENT),
                message="This document has already been uploaded.",
                duplicate_of=DuplicateRef(
                    document_id=existing.id, file_name=existing.original_file_name
                ),
            )

        # 10-11. Row first, then blob. A crash between them leaves a visible,
        #        recoverable row rather than an invisible orphaned file.
        storage_key = _storage_key(extension)
        try:
            document = self.documents.create(
            organization_id=self.organization_id,
                file_name=safe_name,
                original_file_name=raw_name[:255],
                file_type=extension,
                mime_type=MIME_TYPES.get(extension, "application/octet-stream"),
                file_size=size_bytes,
                storage_key=storage_key,
                file_hash=file_hash,
                created_by=user_id,
                status=DocumentStatus.QUEUED,
            )
            self.storage.save(storage_key, stream)

            self.audit.record(
                organization_id=self.organization_id,
                action=AuditAction.DOCUMENT_UPLOAD,
                entity_type="document",
                entity_id=document.id,
                user_id=user_id,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
                # Filename and size only. File contents are never audited (§39).
                metadata={"file_name": safe_name, "size_bytes": size_bytes,
                          "extension": extension},
            )
            self.db.commit()

        except Exception:
            self.db.rollback()
            # The blob may have been written before the failure; remove it so a
            # failed upload cannot leak disk.
            try:
                self.storage.delete(storage_key)
            except Exception:  # pragma: no cover - best effort cleanup
                pass
            logger.exception("Upload failed", extra={"file_name": safe_name})
            return FileResult(
                file_name=safe_name,
                status=UploadOutcome.REJECTED,
                error_code=str(ErrorCode.INTERNAL_ERROR),
                message="The file could not be stored.",
            )

        seen_hashes[file_hash] = safe_name

        # 12. Enqueue AFTER the commit. Enqueueing first lets a fast worker pick
        #     up an id its own transaction cannot yet see.
        task_id = self._enqueue(document.id)

        return FileResult(
            file_name=safe_name,
            status=UploadOutcome.QUEUED,
            document_id=document.id,
            task_id=task_id,
        )

    # ------------------------------------------------------------------ #

    def _enqueue(self, document_id: UUID) -> str | None:
        from app.workers.tasks.process_document import process_document

        try:
            async_result = process_document.delay(str(document_id))
        except Exception:
            # A broker outage must not lose the upload. The row stays QUEUED
            # and can be reprocessed once the broker is back.
            logger.exception("Could not enqueue processing", extra={"document_id": str(document_id)})
            return None

        document = self.documents.get(self.organization_id, document_id)
        if document is not None:
            self.documents.set_task_id(document, async_result.id)
            self.db.commit()
        return async_result.id


def _storage_key(extension: str) -> str:
    """Generated, never derived from the uploaded name (§45).

    Date-partitioned so one directory does not accumulate every document.
    """
    now = datetime.now(timezone.utc)
    return f"{now:%Y/%m}/{uuid4()}.{extension}"


def _rejected(file_name: str, failure: ValidationFailure) -> FileResult:
    return FileResult(
        file_name=file_name or "(unnamed)",
        status=UploadOutcome.REJECTED,
        error_code=str(failure.error_code),
        message=failure.message,
    )
