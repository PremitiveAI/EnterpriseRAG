"""Database access for documents. No business rules here (spec §12)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.modules.documents.models import (
    Document,
    DocumentCategory,
    DocumentChunk,
    DocumentProcessing,
    DocumentStatus,
    DocumentTag,
    TagSource,
)

# Sortable columns are a fixed map, not a string dropped into ORDER BY. A column
# name taken from a query string is an injection vector even when the *values*
# are parameterised — parameters bind values, never identifiers.
SORTABLE = {
    "created_at": Document.created_at,
    "file_name": Document.original_file_name,
    "file_size": Document.file_size,
    "status": Document.status,
}

SortField = Literal["created_at", "file_name", "file_size", "status"]


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so a search for "100%" is not a match-everything."""
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


@dataclass
class ListQuery:
    """Filters for the document list.

    ``organization_id`` has no default on purpose. An optional tenant filter
    fails open: one forgotten call site returns every tenant's rows, in
    production, with no error and nothing to see in review
    (docs/release-2/features/tenant-isolation.md).
    """

    organization_id: UUID
    search: str | None = None
    status: list[DocumentStatus] = field(default_factory=list)
    category_id: list[UUID] = field(default_factory=list)
    document_type: str | None = None
    language: str | None = None
    tag: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    sort: SortField = "created_at"
    order: Literal["asc", "desc"] = "desc"
    page: int = 1
    page_size: int = 25
    include_deleted: bool = False


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(
        self,
        organization_id: UUID,
        document_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> Document | None:
        """Fetch within a tenant. Another organization's id simply misses,
        which is what lets the caller return 404 rather than 403."""
        stmt = (
            select(Document)
            .where(Document.organization_id == organization_id)
            .where(Document.id == document_id)
        )
        if not include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))
        return self.db.execute(stmt).scalar_one_or_none()

    def find_by_file_hash(self, organization_id: UUID, file_hash: str) -> Document | None:
        """Level-1 duplicate lookup.

        Restricted to live rows: re-uploading a document that was deleted is
        legitimate, so a soft-deleted match must not block it (ADR-005).
        """
        stmt = (
            select(Document)
            .where(Document.organization_id == organization_id)
            .where(Document.file_hash == file_hash)
            .where(Document.deleted_at.is_(None))
            .order_by(Document.created_at)
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def find_by_content_hash(
        self, organization_id: UUID, content_hash: str, *, exclude_id: UUID
    ) -> Document | None:
        """Level-2 lookup, used by the Celery task in Phase 4.

        Only COMPLETED documents can be cited as the original — a half-processed
        row must not become the canonical copy.
        """
        stmt = (
            select(Document)
            .where(Document.organization_id == organization_id)
            .where(Document.content_hash == content_hash)
            .where(Document.id != exclude_id)
            .where(Document.deleted_at.is_(None))
            .where(Document.status == DocumentStatus.COMPLETED)
            .order_by(Document.created_at)
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def create(
        self,
        *,
        file_name: str,
        original_file_name: str,
        file_type: str,
        mime_type: str,
        file_size: int,
        storage_key: str,
        file_hash: str,
        created_by: UUID,
        organization_id: UUID,
        status: DocumentStatus = DocumentStatus.QUEUED,
    ) -> Document:
        document = Document(
            organization_id=organization_id,
            file_name=file_name,
            original_file_name=original_file_name,
            file_type=file_type,
            mime_type=mime_type,
            file_size=file_size,
            storage_key=storage_key,
            file_hash=file_hash,
            created_by=created_by,
            status=status,
        )
        self.db.add(document)
        self.db.flush()

        # The 1:1 processing row is created with the document, so the status
        # endpoint never has to handle a missing relation.
        self.db.add(DocumentProcessing(document_id=document.id))
        self.db.flush()
        return document

    def set_task_id(self, document: Document, task_id: str) -> None:
        if document.processing is not None:
            document.processing.task_id = task_id
            self.db.flush()

    # --- Listing (spec §28, docs/features/document-management.md) --------- #

    def list_documents(self, query: ListQuery) -> tuple[list[Document], int]:
        """Filtered, sorted, paginated list plus the unpaginated total.

        The count runs on the same filtered statement, so `total` and `items`
        can never disagree about what the filters mean.
        """
        # selectinload, not lazy: a failed row shows its error_code, and without
        # this the list issues one extra query per row to fetch it.
        stmt = select(Document).options(selectinload(Document.processing))
        stmt = self._apply_filters(stmt, query)

        total = self.db.execute(
            select(func.count()).select_from(self._apply_filters(select(Document.id), query).subquery())
        ).scalar_one()

        column = SORTABLE[query.sort]
        direction = column.asc() if query.order == "asc" else column.desc()
        # Tie-break on id: without it, two rows sharing a created_at can swap
        # between pages and a document is shown twice or never.
        stmt = stmt.order_by(direction, Document.id.asc())
        stmt = stmt.offset((query.page - 1) * query.page_size).limit(query.page_size)

        items = list(self.db.execute(stmt).unique().scalars())
        return items, total

    def _apply_filters(self, stmt, query: ListQuery):
        # FIRST, always. Every other condition is optional; this one is not.
        stmt = stmt.where(Document.organization_id == query.organization_id)

        if not query.include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))

        if query.search:
            # ILIKE on both the AI title and the name the admin actually
            # uploaded — searching for the filename must work even when the
            # AI renamed the document.
            pattern = f"%{escape_like(query.search.strip())}%"
            stmt = stmt.where(
                or_(
                    Document.title.ilike(pattern, escape="\\"),
                    Document.original_file_name.ilike(pattern, escape="\\"),
                )
            )

        if query.status:
            stmt = stmt.where(Document.status.in_(query.status))
        if query.category_id:
            stmt = stmt.where(Document.category_id.in_(query.category_id))
        if query.document_type:
            stmt = stmt.where(Document.document_type == query.document_type)
        if query.language:
            stmt = stmt.where(Document.language == query.language)
        if query.created_from:
            stmt = stmt.where(Document.created_at >= query.created_from)
        if query.created_to:
            stmt = stmt.where(Document.created_at <= query.created_to)
        if query.tag:
            stmt = stmt.where(
                Document.id.in_(
                    select(DocumentTag.document_id).where(DocumentTag.tag == query.tag.lower())
                )
            )
        return stmt

    def chunk_counts(self, organization_id: UUID,
                     document_ids: list[UUID]) -> dict[UUID, int]:
        """One grouped query for the whole page, rather than N per-row counts."""
        if not document_ids:
            return {}
        rows = self.db.execute(
            select(DocumentChunk.document_id, func.count())
            .where(DocumentChunk.organization_id == organization_id)
            .where(DocumentChunk.document_id.in_(document_ids))
            .group_by(DocumentChunk.document_id)
        ).all()
        return {row[0]: row[1] for row in rows}

    def tags_for(self, organization_id: UUID,
                 document_ids: list[UUID]) -> dict[UUID, list[str]]:
        if not document_ids:
            return {}
        rows = self.db.execute(
            select(DocumentTag.document_id, DocumentTag.tag)
            .join(Document, Document.id == DocumentTag.document_id)
            .where(Document.organization_id == organization_id)
            .where(DocumentTag.document_id.in_(document_ids))
            .order_by(DocumentTag.tag)
        ).all()
        grouped: dict[UUID, list[str]] = {}
        for document_id, tag in rows:
            grouped.setdefault(document_id, []).append(tag)
        return grouped

    def distinct_document_types(self, organization_id: UUID) -> list[str]:
        rows = self.db.execute(
            select(Document.document_type)
            .where(Document.organization_id == organization_id)
            .where(Document.document_type.is_not(None))
            .where(Document.deleted_at.is_(None))
            .distinct()
            .order_by(Document.document_type)
        ).scalars()
        return [r for r in rows if r]

    def distinct_languages(self, organization_id: UUID) -> list[str]:
        rows = self.db.execute(
            select(Document.language)
            .where(Document.organization_id == organization_id)
            .where(Document.language.is_not(None))
            .where(Document.deleted_at.is_(None))
            .distinct()
            .order_by(Document.language)
        ).scalars()
        return [r for r in rows if r]

    def active_categories(self) -> list[DocumentCategory]:
        return list(
            self.db.execute(
                select(DocumentCategory)
                .where(DocumentCategory.is_active.is_(True))
                .order_by(DocumentCategory.sort_order, DocumentCategory.name)
            ).scalars()
        )

    def get_category(self, category_id: UUID) -> DocumentCategory | None:
        return self.db.get(DocumentCategory, category_id)

    def replace_tags(self, document: Document, tags: list[str], source: TagSource) -> None:
        """Tags are replaced wholesale, never appended.

        An edit that appended would make every save grow the tag list, and a
        reprocess would then double the AI tags.
        """
        self.db.execute(delete(DocumentTag).where(DocumentTag.document_id == document.id))
        for tag in tags:
            self.db.add(DocumentTag(document_id=document.id, tag=tag, source=source))
        self.db.flush()

    def delete_chunks(self, document: Document) -> int:
        result = self.db.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
        return result.rowcount or 0
