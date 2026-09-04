"""Organization, organization admin and OTP models (Release 2).

Schema of record: docs/release-2/README.md §5.

Three tables, and one design decision worth stating at the top: Organization
Admins live in their own table rather than gaining a ``role`` column on
``users``. That keeps the Release 1 Super Admin row untouched, and it is what
makes the privacy guarantee structural — an Organization Admin has no password
column at all, so a Super Admin has no credential to set or reset.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrganizationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class IdentifierType(StrEnum):
    EMAIL = "EMAIL"
    MOBILE = "MOBILE"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant.

    ``id`` appears in the public chat URL, so it is public knowledge by design.
    Isolation therefore rests on enforcement, never on the id being secret.
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Immutable after creation: it names the Qdrant collection.
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    status: Mapped[OrganizationStatus] = mapped_column(
        SAEnum(OrganizationStatus, name="organization_status", native_enum=True),
        nullable=False,
        default=OrganizationStatus.ACTIVE,
    )
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Limits. NULL means "inherit the global .env default", never
    # "unlimited" — the settings service resolves the fallback. ---
    max_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_document_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_chat: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rate_limit_upload: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rate_limit_public_chat: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- Public chatbot ---
    public_chat_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Opaque, server-generated. The embed snippet keys on this rather than on
    # the slug, so renaming never breaks a live widget.
    public_chat_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    public_chat_greeting: Mapped[str | None] = mapped_column(String(500), nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    admins: Mapped[list[OrganizationAdmin]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.status == OrganizationStatus.ACTIVE and self.deleted_at is None

    @property
    def collection_name(self) -> str:
        """The Qdrant collection for this tenant.

        Derived from the id rather than the slug: a slug is human-editable in
        principle, and a collection name that can change is a collection that
        can be orphaned.
        """
        return f"org_{str(self.id).replace('-', '')}"


class OrganizationAdmin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant administrator. Authenticates by OTP.

    There is deliberately **no password column**. See the module docstring.
    """

    __tablename__ = "organization_admins"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # GLOBALLY unique, not unique per organization: an OTP identifier must
    # resolve to exactly one admin, or login is ambiguous across tenants.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    mobile: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped[Organization] = relationship(back_populates="admins", lazy="joined")


class OtpRequest(UUIDPrimaryKeyMixin, Base):
    """One OTP issue attempt.

    A row is written even when the identifier matches nothing. Short-circuiting
    would make an unknown identifier measurably faster to reject than a known
    one, which is an enumeration oracle — and the row is also the record of the
    probe.
    """

    __tablename__ = "otp_requests"

    identifier: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    identifier_type: Mapped[IdentifierType] = mapped_column(
        SAEnum(IdentifierType, name="identifier_type", native_enum=True), nullable=False
    )
    # NULL when the identifier resolved to nothing.
    organization_admin_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization_admins.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Hashed. A database read must not yield a working code.
    otp_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)

    # Immutable rows: created_at only, matching chat_messages. func.now() rather
    # than the string "now()", so `alembic check` does not report a phantom
    # default change on every run.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # The verify path: newest unexpired row for an identifier.
        Index("ix_otp_requests_identifier_created", "identifier", "created_at"),
    )
