"""Upload contracts (docs/features/document-upload.md §7)."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class UploadOutcome(StrEnum):
    QUEUED = "QUEUED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class DuplicateRef(BaseModel):
    document_id: UUID
    file_name: str


class FileResult(BaseModel):
    """One entry per submitted file. A batch is partially successful (§19)."""

    file_name: str
    status: UploadOutcome
    document_id: UUID | None = None
    task_id: str | None = None
    error_code: str | None = None
    message: str | None = None
    duplicate_of: DuplicateRef | None = None


class UploadSummary(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    results: list[FileResult]


class UploadLimits(BaseModel):
    """Served to the frontend so client validation cannot drift from server rules."""

    max_document_size_mb: int
    max_image_size_mb: int
    max_files_per_batch: int
    document_extensions: list[str]
    image_extensions: list[str]
    legacy_extensions: list[str]
