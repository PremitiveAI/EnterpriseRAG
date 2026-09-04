"""Database access for organizations. No business rules here (spec §12)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.documents.models import Document, DocumentStatus
from app.modules.organizations.models import Organization, OrganizationStatus


class OrganizationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        name: str,
        slug: str,
        public_chat_key: str,
        contact_email: str | None = None,
        **limits: object,
    ) -> Organization:
        organization = Organization(
            name=name,
            slug=slug,
            public_chat_key=public_chat_key,
            contact_email=contact_email,
            status=OrganizationStatus.ACTIVE,
            **limits,
        )
        self.db.add(organization)
        self.db.flush()
        return organization

    def get(self, organization_id: UUID, *, include_deleted: bool = False) -> Organization | None:
        stmt = select(Organization).where(Organization.id == organization_id)
        if not include_deleted:
            stmt = stmt.where(Organization.deleted_at.is_(None))
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_slug(self, slug: str) -> Organization | None:
        return self.db.execute(
            select(Organization).where(Organization.slug == slug)
        ).scalar_one_or_none()

    def get_by_public_key(self, public_chat_key: str) -> Organization | None:
        return self.db.execute(
            select(Organization)
            .where(Organization.public_chat_key == public_chat_key)
            .where(Organization.deleted_at.is_(None))
        ).scalar_one_or_none()

    def list(
        self,
        *,
        search: str | None = None,
        status: OrganizationStatus | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Organization], int]:
        stmt = select(Organization).where(Organization.deleted_at.is_(None))

        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(Organization.name.ilike(pattern), Organization.slug.ilike(pattern))
            )
        if status is not None:
            stmt = stmt.where(Organization.status == status)

        total = self.db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()

        # Tie-break on id: without it two organizations sharing a created_at can
        # swap between pages and one is shown twice or never.
        stmt = (
            stmt.order_by(Organization.created_at.desc(), Organization.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.db.execute(stmt).unique().scalars()), total

    def counts_for(self, organization_id: UUID) -> dict[str, int]:
        """Counts only — never titles, filenames or content.

        This is the mechanism behind the Super Admin privacy guarantee, not a UI
        choice: browsing an organization's document names is already reading its
        data.
        """
        from app.modules.chat.models import Conversation
        from app.modules.organizations.models import OrganizationAdmin

        def count(model, *conditions) -> int:
            stmt = select(func.count()).select_from(model).where(*conditions)
            return self.db.execute(stmt).scalar_one()

        return {
            "admins": count(
                OrganizationAdmin,
                OrganizationAdmin.organization_id == organization_id,
                OrganizationAdmin.is_active.is_(True),
            ),
            "documents": count(
                Document,
                Document.organization_id == organization_id,
                Document.deleted_at.is_(None),
            ),
            "public_documents": count(
                Document,
                Document.organization_id == organization_id,
                Document.deleted_at.is_(None),
                Document.is_public.is_(True),
            ),
            "conversations": count(
                Conversation,
                Conversation.organization_id == organization_id,
                Conversation.deleted_at.is_(None),
            ),
        }

    def has_documents(self, organization_id: UUID) -> bool:
        return self.db.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.organization_id == organization_id)
            .where(Document.deleted_at.is_(None))
        ).scalar_one() > 0

    def document_count(self, organization_id: UUID) -> int:
        return self.db.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.organization_id == organization_id)
            .where(Document.deleted_at.is_(None))
            .where(Document.status != DocumentStatus.DELETED)
        ).scalar_one()

    def set_status(self, organization: Organization, status: OrganizationStatus) -> None:
        organization.status = status
        self.db.flush()

    def soft_delete(self, organization: Organization) -> None:
        organization.status = OrganizationStatus.DELETED
        organization.deleted_at = datetime.now(timezone.utc)
        self.db.flush()
