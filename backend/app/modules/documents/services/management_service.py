"""List, detail, edit, delete, reprocess and download (spec §28-§30).

Business rules live here. The controller translates HTTP into these calls and
nothing else (§12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import ConflictError, NotFoundError, StorageError, ValidationError
from app.core.logging import get_logger
from app.modules.auth.models import AuditAction
from app.modules.auth.repositories.user_repository import AuditRepository
from app.modules.documents.models import (
    TERMINAL_STATUSES,
    Document,
    DocumentStatus,
    TagSource,
)
from app.modules.documents.repositories.document_repository import (
    DocumentRepository,
    ListQuery,
)
from app.modules.documents.schemas.management import (
    CategoryRef,
    DocumentDetail,
    DocumentListResponse,
    DocumentSummary,
    DocumentUpdateRequest,
    FilterOptions,
    ProcessingInfo,
    ReprocessResponse,
)
from app.modules.documents.services.state_machine import transition
from app.storage.base import StorageService
from app.utils.file_validation import sanitise_filename
from app.vector import repository as vector_repo
from config.settings import settings

logger = get_logger(__name__)

S = DocumentStatus

# Only a document that has stopped moving may be edited or reprocessed. QUEUED
# counts as moving: a worker can claim it a millisecond later, and the pipeline
# would overwrite the edit with AI-generated metadata (§29).
REPROCESSABLE: frozenset[DocumentStatus] = frozenset({
    S.COMPLETED,
    S.FAILED,
    S.DUPLICATE,
})


@dataclass
class Download:
    stream: BinaryIO
    file_name: str
    mime_type: str
    size: int


@dataclass
class RequestContext:
    """Who did it and from where — attached to every audit row (§40)."""

    user_id: UUID | None = None
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class DocumentManagementService:
    """Scoped to one organization for the life of the request.

    The tenant is a constructor argument rather than a per-method parameter:
    there is then no method that can be called without it, and no call site that
    can forget it (docs/release-2/features/tenant-isolation.md).
    """

    def __init__(
        self, db: Session, storage: StorageService, organization_id: UUID
    ) -> None:
        self.db = db
        self.storage = storage
        self.organization_id = organization_id
        self.documents = DocumentRepository(db)
        self.audit = AuditRepository(db)

    # --- Read ---------------------------------------------------------- #

    def list(self, query: ListQuery) -> DocumentListResponse:
        items, total = self.documents.list_documents(query)
        ids = [d.id for d in items]

        # Two batched queries for the whole page rather than two per row.
        chunk_counts = self.documents.chunk_counts(self.organization_id, ids)
        tags = self.documents.tags_for(self.organization_id, ids)

        total_pages = (total + query.page_size - 1) // query.page_size if total else 0

        return DocumentListResponse(
            items=[
                self._summary(d, chunk_counts.get(d.id, 0), tags.get(d.id, []))
                for d in items
            ],
            page=query.page,
            page_size=query.page_size,
            total=total,
            total_pages=total_pages,
        )

    def detail(self, document_id: UUID) -> DocumentDetail:
        document = self._require(document_id)
        chunk_count = self.documents.chunk_counts(self.organization_id, [document.id]).get(document.id, 0)
        tags = self.documents.tags_for(self.organization_id, [document.id]).get(document.id, [])

        processing = document.processing
        return DocumentDetail(
            **self._summary(document, chunk_count, tags).model_dump(),
            description=document.description,
            mime_type=document.mime_type,
            original_file_name=document.original_file_name,
            duplicate_of=document.duplicate_of_document_id,
            updated_at=document.updated_at,
            processing=ProcessingInfo(
                current_stage=processing.current_stage,
                error_code=processing.error_code,
                error_message=processing.processing_error,
                retry_count=processing.retry_count,
                duration_ms=processing.duration_ms,
                started_at=processing.processing_started_at,
                completed_at=processing.processing_completed_at,
            ) if processing else None,
        )

    def filter_options(self) -> FilterOptions:
        return FilterOptions(
            categories=[
                CategoryRef(id=c.id, slug=c.slug, name=c.name)
                for c in self.documents.active_categories()
            ],
            statuses=[str(s) for s in DocumentStatus],
            document_types=self.documents.distinct_document_types(self.organization_id),
            languages=self.documents.distinct_languages(self.organization_id),
        )

    def download(self, document_id: UUID, context: RequestContext) -> Download:
        document = self._require(document_id)

        if not self.storage.exists(document.storage_key):
            raise StorageError(
                "The stored file is no longer available.",
                error_code=ErrorCode.STORAGE_FILE_MISSING,
            )

        self._audit(AuditAction.DOCUMENT_DOWNLOADED, document, context)
        self.db.commit()

        return Download(
            stream=self.storage.open(document.storage_key),
            # Re-sanitised on the way out: the stored name came from a client,
            # and this one lands in a Content-Disposition header.
            file_name=sanitise_filename(document.original_file_name),
            mime_type=document.mime_type,
            size=document.file_size,
        )

    # --- Edit ---------------------------------------------------------- #

    def update(
        self, document_id: UUID, payload: DocumentUpdateRequest, context: RequestContext
    ) -> DocumentDetail:
        document = self._require(document_id)

        if document.status not in TERMINAL_STATUSES:
            raise ConflictError(
                "This document is still being processed. Editing now would be overwritten.",
                error_code=ErrorCode.DOCUMENT_NOT_EDITABLE,
                details={"status": str(document.status)},
            )

        changed: dict[str, object] = {}

        if payload.title is not None and payload.title != document.title:
            document.title = payload.title
            changed["title"] = payload.title
        if payload.description is not None and payload.description != document.description:
            document.description = payload.description
            changed["description"] = "(updated)"
        if payload.document_type is not None and payload.document_type != document.document_type:
            document.document_type = payload.document_type
            changed["document_type"] = payload.document_type

        if "category_id" in payload.model_fields_set:
            document.category_id = self._resolve_category(payload.category_id)
            changed["category_id"] = str(payload.category_id) if payload.category_id else None

        if payload.tags is not None:
            self.documents.replace_tags(document, payload.tags, TagSource.MANUAL)
            changed["tags"] = payload.tags

        if not changed:
            # Nothing to write, nothing to audit, and no reason to touch Qdrant.
            return self.detail(document_id)

        self._audit(AuditAction.DOCUMENT_UPDATED, document, context,
                    metadata={"fields": sorted(changed)})
        self.db.commit()

        # Sessions are configured with expire_on_commit=False, so the joined
        # `category` relationship still holds the pre-edit value here. Without
        # this refresh the response — and the Qdrant payload written below —
        # would carry the OLD category while the database holds the new one.
        self.db.refresh(document)

        # The payload carries the values retrieval renders — name, category,
        # tags. The VECTORS are untouched: the text has not changed (§29).
        self._sync_payload(document, payload.tags)

        logger.info("Document metadata updated",
                    extra={"document_id": str(document.id), "fields": sorted(changed)})
        return self.detail(document_id)

    def set_public(
        self, document_id: UUID, is_public: bool, context: RequestContext
    ) -> DocumentDetail:
        """Publish or unpublish a document to the organization's chatbot.

        Only a COMPLETED document can be published: anything earlier has no
        vectors, so publishing it would advertise an empty corpus.

        This does NOT re-embed. The text has not changed, so re-running the
        encoder would spend a CPU-bound minute producing identical numbers -
        the same reasoning as a metadata-only edit (§29). What it does update is
        the Qdrant payload, which is what the public search filters on.
        """
        document = self._require(document_id)

        if is_public and document.status != S.COMPLETED:
            raise ConflictError(
                "Only a processed document can be published.",
                error_code=ErrorCode.DOCUMENT_NOT_PUBLISHABLE,
                details={"status": str(document.status)},
            )

        if document.is_public == is_public:
            return self.detail(document_id)

        document.is_public = is_public
        self._audit(
            AuditAction.DOCUMENT_PUBLISHED if is_public else AuditAction.DOCUMENT_UNPUBLISHED,
            document, context, metadata={"is_public": is_public},
        )
        self.db.commit()

        # The payload carries the flag the public chatbot filters on. Skip this
        # and the database says published while retrieval disagrees.
        try:
            vector_repo.set_document_payload(
                document.organization_id, document.id, {"is_public": is_public}
            )
        except Exception:
            logger.exception("Qdrant payload not updated after publish",
                             extra={"document_id": str(document.id)})

        logger.info("Document publish state changed",
                    extra={"document_id": str(document.id), "is_public": is_public})
        return self.detail(document_id)

    # --- Delete -------------------------------------------------------- #

    def delete(self, document_id: UUID, context: RequestContext) -> dict[str, object]:
        document = self.documents.get(self.organization_id, document_id, include_deleted=True)
        if document is None:
            raise NotFoundError("No document with that id.",
                                error_code=ErrorCode.DOCUMENT_NOT_FOUND)
        if document.is_deleted:
            raise ConflictError("This document has already been deleted.",
                                error_code=ErrorCode.DOCUMENT_ALREADY_DELETED)

        document.deleted_at = datetime.now(timezone.utc)
        transition(self.db, document, S.DELETED, stage="deleted", commit=False)
        self._audit(AuditAction.DOCUMENT_DELETED, document, context,
                    metadata={"file_name": document.original_file_name})
        self.db.commit()

        # Database first, vectors second. If this fails the document is already
        # invisible to retrieval — the status filter excludes anything that is
        # not COMPLETED — so the failure degrades safely. The reverse order
        # leaves a window where the vectors are gone but the document is live.
        vectors_removed = True
        try:
            vector_repo.delete_document_vectors(document.organization_id, document.id)
        except Exception:
            vectors_removed = False
            logger.exception("Vectors not removed on delete — retrieval still excludes them",
                             extra={"document_id": str(document.id)})

        if settings.DELETE_SOURCE_FILE_ON_DELETE:
            try:
                self.storage.delete(document.storage_key)
            except Exception:
                logger.warning("Could not delete source blob",
                               extra={"document_id": str(document.id)})

        return {"document_id": str(document.id), "status": str(S.DELETED),
                "vectors_removed": vectors_removed}

    # --- Reprocess ----------------------------------------------------- #

    def reprocess(self, document_id: UUID, context: RequestContext) -> ReprocessResponse:
        document = self._require(document_id)

        if document.status not in REPROCESSABLE:
            raise ConflictError(
                "This document is already queued or being processed.",
                error_code=ErrorCode.DOCUMENT_NOT_REPROCESSABLE,
                details={"status": str(document.status)},
            )

        if not self.storage.exists(document.storage_key):
            raise ValidationError(
                "The source file is missing, so this document cannot be reprocessed.",
                error_code=ErrorCode.STORAGE_FILE_MISSING,
                status_code=409,
            )

        # Old vectors go BEFORE re-indexing. If the reprocessed document yields
        # fewer chunks, the surplus points would otherwise stay searchable
        # forever, citing text the document no longer contains (§29).
        vector_repo.delete_document_vectors(document.organization_id, document.id)
        removed = self.documents.delete_chunks(document)

        # A stale error on a requeued document reads as a fresh failure.
        if document.processing is not None:
            document.processing.error_code = None
            document.processing.processing_error = None
            document.processing.retry_count = 0
            document.processing.processing_started_at = None
            document.processing.processing_completed_at = None
            document.processing.duration_ms = None

        # Cleared so the second-level duplicate check re-runs from scratch: the
        # original may itself have been deleted since.
        document.content_hash = None
        document.duplicate_of_document_id = None

        transition(self.db, document, S.QUEUED, stage="requeued", commit=False)
        self._audit(AuditAction.DOCUMENT_REPROCESSED, document, context,
                    metadata={"removed_chunks": removed})
        self.db.commit()

        task_id = self._enqueue(document.id)

        logger.info("Document requeued",
                    extra={"document_id": str(document.id), "removed_chunks": removed})
        return ReprocessResponse(document_id=document.id, status=str(S.QUEUED),
                                 task_id=task_id, removed_chunks=removed)

    # --- Internals ----------------------------------------------------- #

    def _require(self, document_id: UUID) -> Document:
        document = self.documents.get(self.organization_id, document_id)
        if document is None:
            raise NotFoundError("No document with that id.",
                                error_code=ErrorCode.DOCUMENT_NOT_FOUND)
        return document

    def _resolve_category(self, category_id: UUID | None) -> UUID | None:
        if category_id is None:
            return None
        category = self.documents.get_category(category_id)
        if category is None or not category.is_active:
            raise ValidationError("That category does not exist or is inactive.",
                                  details={"category_id": str(category_id)})
        return category.id

    def _summary(self, document: Document, chunk_count: int, tags: list[str]) -> DocumentSummary:
        category = document.category
        return DocumentSummary(
            id=document.id,
            file_name=document.original_file_name,
            title=document.title,
            file_type=document.file_type,
            file_size=document.file_size,
            document_type=document.document_type,
            category=CategoryRef(id=category.id, slug=category.slug, name=category.name)
            if category else None,
            status=str(document.status),
            language=document.language,
            page_count=document.page_count,
            chunk_count=chunk_count,
            tags=tags,
            is_possible_duplicate=document.is_possible_duplicate,
            is_public=document.is_public,
            # Surfaced on the row itself, so a failure and its Reprocess button
            # sit together rather than a click apart.
            error_code=document.processing.error_code if document.processing else None,
            created_at=document.created_at,
            deleted_at=document.deleted_at,
        )

    def _sync_payload(self, document: Document, tags: list[str] | None) -> None:
        category = document.category
        fields: dict[str, object] = {
            "document_name": document.original_file_name,
            "document_type": document.document_type,
            "category_slug": category.slug if category else None,
        }
        if tags is not None:
            fields["tags"] = tags

        try:
            vector_repo.set_document_payload(document.organization_id, document.id, fields)
        except Exception:
            # A stale payload is a display defect, not a data-loss one, and the
            # edit is already committed. Reprocess rewrites it.
            logger.exception("Qdrant payload not updated after edit",
                             extra={"document_id": str(document.id)})

    def _enqueue(self, document_id: UUID) -> str | None:
        from app.workers.tasks.process_document import process_document

        try:
            async_result = process_document.delay(str(document_id))
        except Exception:
            # A broker outage must not lose the request. The row stays QUEUED
            # and can be reprocessed once the broker is back.
            logger.exception("Could not enqueue reprocessing",
                             extra={"document_id": str(document_id)})
            return None

        document = self.documents.get(self.organization_id, document_id)
        if document is not None:
            self.documents.set_task_id(document, async_result.id)
            self.db.commit()
        return async_result.id

    def _audit(self, action: str, document: Document, context: RequestContext,
               metadata: dict | None = None) -> None:
        self.audit.record(
            organization_id=self.organization_id,
            action=action,
            entity_type="document",
            entity_id=document.id,
            user_id=context.user_id,
            request_id=context.request_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata=metadata or {},
        )
