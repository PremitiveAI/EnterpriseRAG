"""Chat and conversation contracts (docs/api/overview.md)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    message_count: int
    last_message_at: datetime | None = None
    created_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    page: int
    page_size: int
    total: int


class SourceRef(BaseModel):
    """One citation (spec §35).

    `document_deleted` is exposed so the UI can render the chip greyed rather
    than linking to a 404 — a citation in an old answer still resolves, but the
    document behind it may since have been removed.
    """

    document_id: UUID
    document_name: str
    chunk_id: UUID | None = None
    page: int | None = None
    section: str | None = None
    score: float
    rank: int
    document_deleted: bool = False


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    is_grounded: bool | None = None
    error_code: str | None = None
    latency_ms: int | None = None
    created_at: datetime
    sources: list[SourceRef] = Field(default_factory=list)


class ConversationDetail(BaseModel):
    id: UUID
    title: str
    message_count: int
    last_message_at: datetime | None = None
    created_at: datetime
    messages: list[MessageResponse]


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class SendMessageRequest(BaseModel):
    # Bounds are checked in the controller too, so the error carries
    # MESSAGE_EMPTY / MESSAGE_TOO_LONG rather than a generic validation error.
    content: str


class AnswerResponse(BaseModel):
    """The answer to one question.

    `is_grounded=false` with an empty `sources` list is a CORRECT outcome, not
    an error — it is §34's refusal. It arrives with HTTP 200.
    """

    message_id: UUID
    conversation_id: UUID
    answer: str
    is_grounded: bool
    sources: list[SourceRef] = Field(default_factory=list)
    latency_ms: int
    error_code: str | None = None
    # True when an agent fell back to its non-agent path. Surfaced so a
    # degraded answer is visible in the response rather than only in a log.
    degraded: bool = False
