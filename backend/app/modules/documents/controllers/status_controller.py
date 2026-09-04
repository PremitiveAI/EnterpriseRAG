"""Status lookup for polling (spec §44)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import NotFoundError
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.schemas.status import DocumentStatusResponse, DuplicateRef
from app.modules.documents.services.state_machine import is_terminal, progress_percent


class StatusController:
    def __init__(self, db: Session, organization_id: UUID) -> None:
        self.db = db
        self.organization_id = organization_id
        self.documents = DocumentRepository(db)

    def get(self, document_id: UUID) -> dict:
        document = self.documents.get(self.organization_id, document_id)
        if document is None:
            raise NotFoundError(
                "No document with that id.", error_code=ErrorCode.DOCUMENT_NOT_FOUND
            )

        processing = document.processing
        duplicate = None
        if document.duplicate_of_document_id:
            original = self.documents.get(
                self.organization_id,
                document.duplicate_of_document_id,
                include_deleted=True,
            )
            if original is not None:
                duplicate = DuplicateRef(
                    document_id=original.id, file_name=original.original_file_name
                )

        body = DocumentStatusResponse(
            document_id=document.id,
            status=str(document.status),
            current_stage=processing.current_stage if processing else None,
            progress_percent=progress_percent(document.status),
            retry_count=processing.retry_count if processing else 0,
            error_code=processing.error_code if processing else None,
            error_message=processing.processing_error if processing else None,
            is_terminal=is_terminal(document.status),
            duplicate_of=duplicate,
        )
        return {"success": True, "data": body.model_dump(mode="json")}
