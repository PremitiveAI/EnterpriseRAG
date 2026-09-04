"""Category master contracts (docs/release-2/features/category-management.md).

The taxonomy is global: no ``organization_id`` appears anywhere in this file,
and that is a decision, not an omission. Every tenant classifies against the
same list so that a category means the same thing everywhere.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class CreateCategoryRequest(BaseModel):
    slug: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=2, max_length=120)
    # Capped well below the model's context on purpose: this text goes into the
    # classifier prompt, so a runaway description would crowd out the document
    # it is meant to help classify.
    description: str | None = Field(default=None, max_length=500)
    sort_order: int = Field(default=0, ge=0, le=100_000)

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, value: str) -> str:
        slug = value.strip().lower()
        if not SLUG_PATTERN.match(slug):
            raise ValueError(
                "Slug must be lowercase letters, digits and single hyphens."
            )
        return slug

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class UpdateCategoryRequest(BaseModel):
    """Every field optional.

    ``slug`` is accepted here and then refused by the service, rather than
    dropped from the schema. Pydantic ignores unknown fields by default, so
    omitting it would make a slug rename *appear to succeed* - 200, no mention
    of the field, and the slug unchanged. Accepting it buys an explicit
    ``CATEGORY_SLUG_IMMUTABLE`` that says why.
    """

    slug: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None


class CategorySummary(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None = None
    sort_order: int = 0
    is_active: bool = True
    # Across every organization. A count is not tenant data - it says how many
    # documents carry this label, never which ones or whose.
    document_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class CategoryListResponse(BaseModel):
    items: list[CategorySummary]


class DeleteCategoryResponse(BaseModel):
    """A delete can turn into a deactivation, so the caller is told which."""

    deleted: bool
    deactivated: bool
    document_count: int
