"""Category master queries (docs/release-2/features/category-management.md).

Global by design: nothing here filters by organization, because the taxonomy is
shared. That makes this the one repository in Release 2 whose queries are
correct *without* an ``organization_id``, so the omission is stated rather than
left to be noticed.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.documents.models import Document, DocumentCategory


class CategoryRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list(self) -> list[DocumentCategory]:
        """Everything, including inactive.

        The Super Admin screen must show what it can reactivate; the filter bar
        endpoint for Organization Admins is the one that hides inactive rows.
        """
        return list(
            self.db.execute(
                select(DocumentCategory).order_by(
                    DocumentCategory.sort_order, DocumentCategory.name
                )
            ).scalars()
        )

    def get(self, category_id: UUID) -> DocumentCategory | None:
        return self.db.get(DocumentCategory, category_id)

    def get_by_slug(self, slug: str) -> DocumentCategory | None:
        return self.db.execute(
            select(DocumentCategory).where(DocumentCategory.slug == slug)
        ).scalar_one_or_none()

    def name_taken(self, name: str, *, excluding: UUID | None = None) -> bool:
        """``name`` carries a UNIQUE constraint, so a clash must be caught here.

        Reaching the database would raise IntegrityError and surface as a 500,
        which is the wrong answer for a user typing a name someone else used.
        """
        query = select(DocumentCategory.id).where(
            func.lower(DocumentCategory.name) == name.strip().lower()
        )
        if excluding is not None:
            query = query.where(DocumentCategory.id != excluding)
        return self.db.execute(query).first() is not None

    def document_counts(self) -> dict[UUID, int]:
        """How many live documents carry each category, across all tenants.

        One grouped query rather than a count per row: with ten categories that
        is ten round trips saved, and the screen renders them all at once.

        Deleted documents are excluded - a category whose only documents are in
        the bin should be hard-deletable, not permanently pinned by them.
        """
        rows = self.db.execute(
            select(Document.category_id, func.count(Document.id))
            .where(Document.category_id.is_not(None))
            .where(Document.deleted_at.is_(None))
            .group_by(Document.category_id)
        ).all()
        return {category_id: count for category_id, count in rows if category_id}

    def add(self, category: DocumentCategory) -> DocumentCategory:
        self.db.add(category)
        self.db.flush()
        return category

    def delete(self, category: DocumentCategory) -> None:
        self.db.delete(category)
        self.db.flush()
