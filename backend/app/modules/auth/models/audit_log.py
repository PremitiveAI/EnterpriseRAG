"""Audit log (spec §22, §40).

Append-only. No update or delete path is exposed.

``metadata_`` must never carry file contents, extracted text or PII
(docs/security/pii-handling.md).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditAction:
    """Known action names. Not an enum — new actions must not need a migration."""

    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"

    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_UPDATED = "document.updated"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_PUBLISHED = "document.published"
    DOCUMENT_UNPUBLISHED = "document.unpublished"
    DOCUMENT_REPROCESSED = "document.reprocessed"
    DOCUMENT_DOWNLOADED = "document.downloaded"
    DOCUMENT_DUPLICATE_DETECTED = "document.duplicate_detected"
    DOCUMENT_PII_REVEALED = "document.pii_revealed"

    CONVERSATION_DELETED = "conversation.deleted"


class ActorType(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ORG_ADMIN = "ORG_ADMIN"
    SYSTEM = "SYSTEM"


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"

    # Two actor types, two typed columns rather than one loose id: the foreign
    # key still enforces that the actor exists, which an untyped column cannot.
    actor_type: Mapped[ActorType | None] = mapped_column(
        SAEnum(ActorType, name="audit_actor_type", native_enum=True), nullable=True
    )
    super_admin_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_admin_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization_admins.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Which tenant the action concerned. NULL for Super Admin actions that are
    # not about one organization, such as editing the category master.
    organization_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_org_created", "organization_id", "created_at"),
        Index("ix_audit_logs_super_admin_created", "super_admin_id", "created_at"),
    )
