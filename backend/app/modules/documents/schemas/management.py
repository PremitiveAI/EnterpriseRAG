"""List, detail and edit contracts (spec §28-§30, docs/api/overview.md)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

MAX_TAGS = 20
MAX_TAG_CHARS = 64


class CategoryRef(BaseModel):
    id: UUID
    slug: str
    name: str


class ProcessingInfo(BaseModel):
    current_stage: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    duration_ms: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class DocumentSummary(BaseModel):
    """One list row. Deliberately excludes description and processing detail."""

    id: UUID
    file_name: str
    title: str | None = None
    file_type: str
    file_size: int
    document_type: str | None = None
    category: CategoryRef | None = None
    status: str
    language: str | None = None
    page_count: int | None = None
    chunk_count: int = 0
    tags: list[str] = Field(default_factory=list)
    is_possible_duplicate: bool = False
    # Reachable by the public chatbot. False unless deliberately published.
    is_public: bool = False
    error_code: str | None = None
    created_at: datetime
    deleted_at: datetime | None = None


class DocumentDetail(DocumentSummary):
    description: str | None = None
    mime_type: str
    original_file_name: str
    duplicate_of: UUID | None = None
    processing: ProcessingInfo | None = None
    updated_at: datetime | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class DocumentUpdateRequest(BaseModel):
    """Metadata only. There is no field here that can change the text, because
    changing the text would silently invalidate the vectors (§29)."""

    title: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=4000)
    category_id: UUID | None = None
    document_type: str | None = Field(default=None, max_length=64)
    tags: list[str] | None = None

    @field_validator("title", "description", "document_type")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: list[str] = []
        for tag in value:
            cleaned = tag.strip().lower()[:MAX_TAG_CHARS]
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen[:MAX_TAGS]


class ReprocessResponse(BaseModel):
    document_id: UUID
    status: str
    task_id: str | None = None
    removed_chunks: int = 0


class FilterOptions(BaseModel):
    """Everything the filter bar needs, in one request rather than four."""

    categories: list[CategoryRef]
    statuses: list[str]
    document_types: list[str]
    languages: list[str]


class PublishRequest(BaseModel):
    """Publishing is always explicit - there is no bulk publish, and no default
    that makes a document public as a side effect of anything else."""

    is_public: bool
