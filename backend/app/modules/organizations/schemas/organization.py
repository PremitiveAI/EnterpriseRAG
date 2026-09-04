"""Super Admin organization contracts (docs/release-2/features/organization-management.md)."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class OrganizationCounts(BaseModel):
    """Counts only.

    This is the mechanism behind the Super Admin privacy guarantee, not a UI
    choice: browsing an organization's document titles would already be reading
    its data.
    """

    admins: int = 0
    documents: int = 0
    public_documents: int = 0
    conversations: int = 0


class OrganizationLimits(BaseModel):
    """``None`` means "inherit the global default", never "unlimited"."""

    max_documents: int | None = None
    max_document_size_mb: int | None = None
    rate_limit_chat: str | None = None
    rate_limit_upload: str | None = None
    rate_limit_public_chat: str | None = None


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=2, max_length=120)
    contact_email: str | None = Field(default=None, max_length=255)
    max_documents: int | None = Field(default=None, ge=1)
    max_document_size_mb: int | None = Field(default=None, ge=1)
    public_chat_enabled: bool = False

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, value: str) -> str:
        slug = value.strip().lower()
        if not SLUG_PATTERN.match(slug):
            raise ValueError(
                "Slug must be lowercase letters, digits and single hyphens."
            )
        return slug

    @field_validator("name", "contact_email")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class UpdateOrganizationRequest(BaseModel):
    """Everything optional. The slug is absent by design - it names the Qdrant
    collection, and a collection name that can change is one that can be
    orphaned."""

    name: str | None = Field(default=None, max_length=255)
    contact_email: str | None = Field(default=None, max_length=255)
    max_documents: int | None = Field(default=None, ge=1)
    max_document_size_mb: int | None = Field(default=None, ge=1)
    rate_limit_chat: str | None = Field(default=None, max_length=32)
    rate_limit_upload: str | None = Field(default=None, max_length=32)
    rate_limit_public_chat: str | None = Field(default=None, max_length=32)
    public_chat_enabled: bool | None = None
    public_chat_greeting: str | None = Field(default=None, max_length=500)


class OrganizationSummary(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    contact_email: str | None = None
    public_chat_enabled: bool = False
    counts: OrganizationCounts = OrganizationCounts()
    created_at: datetime


class OrganizationDetail(OrganizationSummary):
    limits: OrganizationLimits = OrganizationLimits()
    # The embed snippet keys on this, not on the slug, so a rename never breaks
    # a live widget.
    public_chat_key: str
    public_chat_greeting: str | None = None
    updated_at: datetime | None = None


class OrganizationListResponse(BaseModel):
    items: list[OrganizationSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


# --- Organization admins -------------------------------------------------- #


class CreateAdminRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: str = Field(min_length=5, max_length=255)
    mobile: str | None = Field(default=None, max_length=20)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        email = value.strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("A valid email address is required.")
        return email


class UpdateAdminRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    mobile: str | None = Field(default=None, max_length=20)


class AdminSummary(BaseModel):
    id: UUID
    full_name: str
    email: str
    mobile: str | None = None
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime
