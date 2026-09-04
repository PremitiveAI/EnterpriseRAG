"""Public chatbot contracts. Deliberately minimal."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PublicChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    # Client-generated. A visitor has no account, so this is the only thing
    # tying their turns together.
    session_id: str = Field(min_length=8, max_length=64)


class PublicSourceRef(BaseModel):
    """Names and pages only - never document_id or chunk_id."""

    document_name: str
    page: int | None = None


class PublicChatResponse(BaseModel):
    answer: str
    is_grounded: bool
    session_id: str
    sources: list[PublicSourceRef] = []


class PublicConfigResponse(BaseModel):
    organization_name: str
    greeting: str
