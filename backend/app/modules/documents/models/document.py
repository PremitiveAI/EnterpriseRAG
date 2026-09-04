"""Document, processing, tag, chunk and category models.

Schema of record: docs/database/schema.md. Business logic for these tables
arrives in Phases 3-5; Phase 2 defines the schema only.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DocumentStatus(StrEnum):
    """Coarse lifecycle state (spec §21).

    Fine-grained stage lives in ``DocumentProcessing.current_stage`` — see
    docs/celery/state-machine.md for why these are two fields.
    """

    UPLOADING = "UPLOADING"
    VALIDATING = "VALIDATING"
    DUPLICATE_CHECK = "DUPLICATE_CHECK"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    EXTRACTING = "EXTRACTING"
    OCR = "OCR"
    CLASSIFYING = "CLASSIFYING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"
    DELETED = "DELETED"


TERMINAL_STATUSES: frozenset[DocumentStatus] = frozenset({
    DocumentStatus.COMPLETED,
    DocumentStatus.FAILED,
    DocumentStatus.DUPLICATE,
    DocumentStatus.DELETED,
})


class TagSource(StrEnum):
    AI = "ai"
    MANUAL = "manual"


class DocumentCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Configurable taxonomy (spec §24). Seeded from config/taxonomy.py."""

    __tablename__ = "document_categories"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    # --- Tenancy (Release 2) ---
    # NOT NULL: there is no such thing as an unowned document. Resolved from the
    # caller's token, never from the request (docs/release-2/features/
    # tenant-isolation.md).
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Reachable by the public chatbot. Defaults to false: publishing is always a
    # deliberate act, never a side effect of uploading.
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Opaque key for StorageService — never an absolute path (spec §45).
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)

    file_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, index=True)
    content_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True, index=True)

    category_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_categories.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, name="document_status", native_enum=True),
        nullable=False, default=DocumentStatus.UPLOADING, index=True,
    )

    duplicate_of_document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    is_possible_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Release 2: uploads come from Organization Admins, never the Super Admin.
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization_admins.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    category: Mapped[DocumentCategory | None] = relationship(lazy="joined")
    processing: Mapped[DocumentProcessing | None] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )
    tags: Mapped[list[DocumentTag]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The hot list query (docs/features/document-management.md).
        # organization_id LEADS every tenant-scoped index. A tenant filter that
        # is not the leading column still scans other tenants' rows before
        # discarding them.
        Index("ix_documents_org_status_deleted", "organization_id", "status", "deleted_at"),
        Index("ix_documents_org_created", "organization_id", "created_at"),
        # The public chatbot's corpus.
        Index("ix_documents_org_public", "organization_id", "is_public"),
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class DocumentProcessing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """1:1 with Document, so the hot list query never reads processing detail."""

    __tablename__ = "document_processing"

    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    task_id: Mapped[str | None] = mapped_column(String(155), nullable=True, index=True)
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Operator-facing. Never a raw stack trace, never PII (spec §38, §39).
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    document: Mapped[Document] = relationship(back_populates="processing")


class DocumentTag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_tags"

    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[TagSource] = mapped_column(
        SAEnum(TagSource, name="tag_source", native_enum=True),
        nullable=False, default=TagSource.AI,
    )

    document: Mapped[Document] = relationship(back_populates="tags")

    __table_args__ = (UniqueConstraint("document_id", "tag", name="uq_document_tag"),)


class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Chunk text is stored here as well as in Qdrant.

    Deliberate: a full re-index then costs an embedding pass rather than an
    extraction and OCR pass (docs/qdrant/collections.md).
    """

    __tablename__ = "document_chunks"

    # Denormalised deliberately. It is derivable by joining documents, but the
    # failure mode of forgetting that join is a silent cross-tenant read, and
    # the cost is one UUID per row.
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Mirrors the deterministic Qdrant point id (ADR-006).
    vector_point_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
    )
