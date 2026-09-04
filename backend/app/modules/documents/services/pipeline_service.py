"""The processing pipeline (spec §22, docs/features/document-processing.md).

Orchestration only — every stage lives in its own service. Called from the
Celery task, but knows nothing about Celery, so it is directly testable.

Idempotency: running this twice on one document converges to the same state.
Chunks and vectors are deleted before being rewritten, and point ids are
deterministic (§23).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.logging import get_logger
from app.modules.ai import enrichment_service, identity_agent
from app.modules.auth.models import AuditAction
from app.modules.auth.repositories.user_repository import AuditRepository
from app.modules.documents.models import (
    Document,
    DocumentCategory,
    DocumentChunk,
    DocumentStatus,
    DocumentTag,
    TagSource,
)
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.services import chunking_service, extraction_service, ocr_service
from app.modules.documents.services.state_machine import transition
from app.storage.base import StorageService
from app.utils.hashing import content_hash
from app.vector import client as vector_client
from app.vector import repository as vector_repo
from app.vector.client import point_id_for
from app.vector.embeddings import embed_texts
from config.settings import settings

logger = get_logger(__name__)

S = DocumentStatus


class RetryableStageError(Exception):
    """Raised for a failure the caller should retry (Qdrant down, etc.)."""

    def __init__(self, message: str, error_code: ErrorCode) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass
class PipelineResult:
    document_id: str
    status: str
    chunks: int = 0
    vectors: int = 0
    pages: int = 0
    ocr_pages: int = 0
    degraded: bool = False
    duplicate_of: str | None = None
    error_code: str | None = None


class ProcessingPipeline:
    def __init__(self, db: Session, storage: StorageService) -> None:
        self.db = db
        self.storage = storage
        self.documents = DocumentRepository(db)
        self.audit = AuditRepository(db)

    # ------------------------------------------------------------------ #

    def run(self, document_id: UUID) -> PipelineResult:
        started = time.perf_counter()

        # Loaded without a tenant filter ON PURPOSE - this is the one place
        # that legitimately does not have one yet. The task carries only a
        # document id, and the organization is read FROM the row, so a
        # malformed message cannot redirect a document into another tenant.
        # populate_existing: sessions use expire_on_commit=False, so a plain
        # `get` can return a cached instance whose deleted_at is stale. The
        # delete check below is a safety gate - reading it from the identity
        # map instead of the database would make that gate depend on whoever
        # happened to load the row first.
        document = self.db.get(Document, document_id, populate_existing=True)
        if document is None:
            # Deleted between enqueue and execution. Not an error.
            return PipelineResult(str(document_id), "skipped")

        # `Session.get` finds soft-deleted rows, which the tenant-filtered
        # repository did not. Without this check a document deleted while its
        # task sat in the queue would be processed and INDEXED anyway - the
        # exact race the "skipped" result exists to close.
        if document.deleted_at is not None or document.status == S.DELETED:
            logger.info("Document deleted before processing began",
                        extra={"document_id": str(document.id)})
            return PipelineResult(str(document_id), "skipped")

        transition(self.db, document, S.PROCESSING, stage="loading")

        # 2. Re-verify the blob. It may have moved or been corrupted between
        #    upload and execution, especially on a retry hours later.
        if not self.storage.exists(document.storage_key):
            return self._fail(document, ErrorCode.STORAGE_FILE_MISSING,
                              "The stored file is missing.")

        # 3-4. Extract, then OCR only if needed.
        transition(self.db, document, S.EXTRACTING, stage="extracting")
        try:
            extraction = self._extract(document)
        except extraction_service.ExtractionError as exc:
            return self._fail(document, ErrorCode.EXTRACTION_FAILED, exc.message)

        ocr_pages = 0
        if extraction.requires_ocr:
            transition(self.db, document, S.OCR, stage="ocr")
            try:
                ocr_pages = self._apply_ocr(document, extraction)
            except ocr_service.OCRUnavailable as exc:
                # Not silently empty: empty text is indistinguishable from a
                # blank page and would poison the content hash (ADR-004).
                return self._fail(document, ErrorCode.OCR_UNAVAILABLE, exc.message)
            except ocr_service.OCRFailed as exc:
                return self._fail(document, ErrorCode.OCR_FAILED, exc.message)

        text = extraction.text
        if not text.strip():
            return self._fail(document, ErrorCode.NO_TEXT_EXTRACTED,
                              "The document parsed successfully but contained no text.")

        document.page_count = extraction.page_count

        # 6. Content duplicate — BEFORE any AI call, so a duplicate costs nothing.
        digest = content_hash(text)
        document.content_hash = digest
        self.db.commit()

        original = self.documents.find_by_content_hash(
            document.organization_id, digest, exclude_id=document.id
        )
        if original is not None:
            return self._mark_duplicate(document, original)

        # 7-10. Language and the AI stages. These degrade, never fail.
        transition(self.db, document, S.CLASSIFYING, stage="classifying")
        document.language = self._detect_language(text)
        enrichment = self._enrich(document, text, extraction, ocr_pages)

        # 11. Chunk.
        transition(self.db, document, S.CHUNKING, stage="chunking")
        chunks = chunking_service.chunk_pages(extraction.pages)
        if not chunks:
            return self._fail(document, ErrorCode.NO_TEXT_EXTRACTED,
                              "No chunks could be produced from this document.")

        chunk_rows = self._persist_chunks(document, chunks)

        # 12. Embed.
        transition(self.db, document, S.EMBEDDING, stage="embedding")
        try:
            vectors = embed_texts([c.text for c in chunks])
        except Exception as exc:
            raise RetryableStageError(str(exc), ErrorCode.EMBEDDING_FAILED) from exc

        # 13. Index.
        transition(self.db, document, S.INDEXING, stage="indexing")
        try:
            indexed = self._index(document, chunks, chunk_rows, vectors, enrichment)
        except Exception as exc:
            raise RetryableStageError(str(exc), ErrorCode.VECTOR_INDEXING_FAILED) from exc

        # 14. Done.
        transition(self.db, document, S.COMPLETED, stage="completed")

        duration = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Processing complete",
            extra={
                "document_id": str(document.id),
                "chunks": len(chunks),
                "vectors": indexed,
                "pages": extraction.page_count,
                "ocr_pages": ocr_pages,
                "degraded": enrichment.degraded,
                "duration_ms": duration,
            },
        )

        return PipelineResult(
            document_id=str(document.id),
            status=str(S.COMPLETED),
            chunks=len(chunks),
            vectors=indexed,
            pages=extraction.page_count,
            ocr_pages=ocr_pages,
            degraded=enrichment.degraded,
        )

    # --- Stages -------------------------------------------------------- #

    def _extract(self, document: Document) -> extraction_service.Extraction:
        with self.storage.open(document.storage_key) as stream:
            return extraction_service.extract(document.file_type, stream)

    def _apply_ocr(self, document: Document, extraction) -> int:
        pages_needing = [p.number for p in extraction.pages if p.needs_ocr]
        if not pages_needing:
            return 0

        if document.file_type in {"png", "jpg", "jpeg", "webp"}:
            with self.storage.open(document.storage_key) as stream:
                text = ocr_service.ocr_image_bytes(stream.read())
            extraction.pages[0].text = text
            return 1

        with self.storage.open(document.storage_key) as stream:
            results = ocr_service.ocr_pdf_pages(stream, pages_needing)

        for page in extraction.pages:
            if page.number in results and results[page.number]:
                page.text = results[page.number]
        return len(results)

    def _detect_language(self, text: str) -> str | None:
        try:
            from langdetect import DetectorFactory, detect

            DetectorFactory.seed = 0  # deterministic
            return detect(text[:2000])
        except Exception:
            logger.info("Language detection failed; leaving unset")
            return None

    def _enrich(self, document: Document, text: str, extraction, ocr_pages: int):
        taxonomy = [
            {"slug": c.slug, "name": c.name, "description": c.description or ""}
            for c in self.db.execute(
                select(DocumentCategory).where(DocumentCategory.is_active.is_(True))
            ).scalars()
        ]

        enrichment = enrichment_service.enrich(
            text, taxonomy=taxonomy, fallback_title=document.original_file_name
        )

        # Agent 1: only for documents that came through OCR, i.e. images and
        # scans — the inputs that can be identity cards.
        if ocr_pages and document.file_type in {"png", "jpg", "jpeg", "webp", "pdf"}:
            identity = identity_agent.analyse(text)
            if identity.document_type:
                enrichment.document_type = identity.document_type

        document.title = enrichment.title
        document.description = enrichment.description
        document.document_type = enrichment.document_type

        if enrichment.category_slug:
            category = self.db.execute(
                select(DocumentCategory).where(DocumentCategory.slug == enrichment.category_slug)
            ).scalar_one_or_none()
            document.category_id = category.id if category else None

        # Tags are REPLACED, never appended — a retry must not double them.
        self.db.execute(delete(DocumentTag).where(DocumentTag.document_id == document.id))
        for tag in enrichment.tags:
            self.db.add(DocumentTag(document_id=document.id, tag=tag, source=TagSource.AI))

        self.db.commit()
        return enrichment

    def _persist_chunks(self, document: Document, chunks) -> list[DocumentChunk]:
        # Deleted then rewritten: a reprocessed document that got shorter must
        # not keep its old tail chunks (§29).
        self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))

        rows = [
            DocumentChunk(
                organization_id=document.organization_id,
                document_id=document.id,
                chunk_index=chunk.index,
                text=chunk.text,
                char_count=chunk.char_count,
                page_number=chunk.page_number,
                section=chunk.section,
                language=document.language,
                vector_point_id=UUID(point_id_for(document.id, chunk.index)),
                metadata_={},
            )
            for chunk in chunks
        ]
        self.db.add_all(rows)
        self.db.commit()
        return rows

    def _index(self, document: Document, chunks, chunk_rows, vectors, enrichment) -> int:
        # Created if absent: an organization's collection is provisioned at
        # creation, but a reprocess after a manual Qdrant reset would otherwise
        # fail on a missing collection.
        vector_client.ensure_collection(document.organization_id)

        # Remove existing points first, so a shorter reprocessed document leaves
        # no orphans pointing at content that no longer exists (§29).
        vector_repo.delete_document_vectors(document.organization_id, document.id)

        category_slug = None
        if document.category_id:
            category = self.db.get(DocumentCategory, document.category_id)
            category_slug = category.slug if category else None

        created = (document.created_at or datetime.now(timezone.utc)).isoformat()

        payloads = [
            vector_repo.ChunkPayload(
                organization_id=str(document.organization_id),
                is_public=document.is_public,
                document_id=str(document.id),
                chunk_id=str(row.id),
                chunk_index=chunk.index,
                text=chunk.text,
                document_name=document.original_file_name,
                document_type=document.document_type,
                category_slug=category_slug,
                language=document.language,
                tags=enrichment.tags,
                page_number=chunk.page_number,
                section=chunk.section,
                # COMPLETED, because the point only becomes searchable once the
                # document reaches that state moments from now.
                status=str(S.COMPLETED),
                created_at=created,
            )
            for chunk, row in zip(chunks, chunk_rows, strict=True)
        ]

        return vector_repo.upsert_chunks(
            document.organization_id, document.id, vectors, payloads
        )

    # --- Terminal outcomes --------------------------------------------- #

    def _mark_duplicate(self, document: Document, original: Document) -> PipelineResult:
        document.duplicate_of_document_id = original.id
        transition(self.db, document, S.DUPLICATE, stage="duplicate_check",
                   error_code=str(ErrorCode.CONTENT_DUPLICATE),
                   error_message=f"Identical content to {original.original_file_name}.")

        if settings.DELETE_DUPLICATE_SOURCE_FILE:
            try:
                self.storage.delete(document.storage_key)
            except Exception:
                logger.warning("Could not delete duplicate blob",
                               extra={"document_id": str(document.id)})

        self.audit.record(
            organization_id=document.organization_id,
            actor_type=None,
            action=AuditAction.DOCUMENT_DUPLICATE_DETECTED,
            entity_type="document",
            entity_id=document.id,
            metadata={"duplicate_of": str(original.id), "level": "content"},
        )
        self.db.commit()

        logger.info("Content duplicate — not indexed",
                    extra={"document_id": str(document.id), "duplicate_of": str(original.id)})

        return PipelineResult(str(document.id), str(S.DUPLICATE),
                              duplicate_of=str(original.id),
                              error_code=str(ErrorCode.CONTENT_DUPLICATE))

    def _fail(self, document: Document, code: ErrorCode, message: str) -> PipelineResult:
        transition(self.db, document, S.FAILED, stage="failed",
                   error_code=str(code), error_message=message)
        logger.warning("Processing failed",
                       extra={"document_id": str(document.id), "error_code": str(code)})
        return PipelineResult(str(document.id), str(S.FAILED), error_code=str(code))
