"""Processing status contract (spec §44)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class DuplicateRef(BaseModel):
    document_id: UUID
    file_name: str


class DocumentStatusResponse(BaseModel):
    """Deliberately small — no joins, no chunk counts. It is polled."""

    document_id: UUID
    status: str
    current_stage: str | None = None
    progress_percent: int
    retry_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    # Exposed so the client never hard-codes the terminal set.
    is_terminal: bool
    duplicate_of: DuplicateRef | None = None
