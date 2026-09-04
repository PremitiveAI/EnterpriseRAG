"""Conversation, message and citation models.

Schema of record: docs/database/schema.md. Business logic arrives in Phase 6.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    # --- Tenancy (Release 2) ---
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # A conversation is owned by an Organization Admin OR by an anonymous public
    # visitor. Never both, never neither - enforced by the CHECK below. Without
    # it a bug could produce a row that no ownership query matches: invisible to
    # its owner and to any cleanup.
    organization_admin_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization_admins.id", ondelete="CASCADE"),
        nullable=True,
    )
    visitor_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New conversation")
    # Denormalised so the sidebar never counts rows.
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Exactly the sidebar query, now tenant-first. organization_id must LEAD:
        # a tenant filter that is not the leading column still scans other
        # tenants' rows before discarding them.
        Index("ix_conversations_org_last_message", "organization_id", "last_message_at"),
        Index("ix_conversations_org_admin", "organization_id", "organization_admin_id"),
        CheckConstraint(
            "(organization_admin_id IS NOT NULL AND visitor_session_id IS NULL)"
            " OR (organization_admin_id IS NULL AND visitor_session_id IS NOT NULL)",
            name="ck_conversations_single_owner",
        ),
    )


class ChatMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chat_messages"

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(MessageRole, name="message_role", native_enum=True), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # False when the model declined for lack of context (spec §34).
    is_grounded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Messages are immutable, so this table carries created_at only and does
    # not use TimestampMixin. func.now() (not the string "now()") keeps the
    # server default identical to every other table, so `alembic check` stays
    # clean instead of reporting a phantom default change on every run.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    sources: Mapped[list[MessageSource]] = relationship(
        back_populates="message", cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_chat_messages_conversation_created", "conversation_id", "created_at"),)


class MessageSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Citations (spec §35).

    A table rather than JSON, so "which documents are cited most" is a query.
    """

    __tablename__ = "message_sources"

    message_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    # RESTRICT, not CASCADE: documents are soft-deleted, so a citation in a
    # year-old answer must still resolve.
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    chunk_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    message: Mapped[ChatMessage] = relationship(back_populates="sources")
