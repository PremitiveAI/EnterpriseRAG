"""Super Admin category master (docs/release-2/features/category-management.md).

Two rules do the real work here.

**The slug is immutable.** The AI classifier is handed the live taxonomy and
returns a slug; that slug is stored on the document and copied into the Qdrant
payload. Renaming it would leave documents and vectors pointing at a category
that no longer exists - a classification silently lost, with no error anywhere
to notice it by. The display name is freely editable; the identifier is not.

**A category in use is deactivated, not deleted.** Hard-deleting one would null
out ``category_id`` on every document that carried it (the foreign key is SET
NULL), so a mis-click would erase classification work across every tenant at
once. Deactivation removes it from the classifier's choices and from filter
bars while the documents that already carry it keep it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.modules.auth.models import ActorType
from app.modules.auth.repositories.user_repository import AuditRepository
from app.modules.documents.models import DocumentCategory
from app.modules.documents.repositories.category_repository import CategoryRepository
from app.modules.documents.schemas.category import (
    CategoryListResponse,
    CategorySummary,
    CreateCategoryRequest,
    DeleteCategoryResponse,
    UpdateCategoryRequest,
)

logger = get_logger(__name__)

CATEGORY_CREATED = "category.created"
CATEGORY_UPDATED = "category.updated"
CATEGORY_DEACTIVATED = "category.deactivated"
CATEGORY_DELETED = "category.deleted"


class CategoryService:
    def __init__(self, db: Session, super_admin_id: UUID) -> None:
        self.db = db
        self.super_admin_id = super_admin_id
        self.categories = CategoryRepository(db)
        self.audit = AuditRepository(db)

    # --- Reads ------------------------------------------------------------ #

    def list(self) -> CategoryListResponse:
        counts = self.categories.document_counts()
        return CategoryListResponse(
            items=[
                self._summary(category, counts.get(category.id, 0))
                for category in self.categories.list()
            ]
        )

    # --- Writes ----------------------------------------------------------- #

    def create(self, payload: CreateCategoryRequest) -> CategorySummary:
        if self.categories.get_by_slug(payload.slug):
            raise ConflictError(
                f"The slug '{payload.slug}' is already in use.",
                error_code=ErrorCode.CATEGORY_SLUG_TAKEN,
            )
        if self.categories.name_taken(payload.name):
            raise ConflictError(
                f"A category named '{payload.name}' already exists.",
                error_code=ErrorCode.CATEGORY_SLUG_TAKEN,
            )

        category = self.categories.add(
            DocumentCategory(
                slug=payload.slug,
                name=payload.name,
                description=payload.description,
                sort_order=payload.sort_order,
                is_active=True,
            )
        )

        self._audit(CATEGORY_CREATED, category, {"slug": category.slug})
        self.db.commit()

        logger.info("Category created", extra={"category_id": str(category.id)})
        # A brand-new category has no documents; skipping the count query here
        # is safe in a way that guessing zero anywhere else would not be.
        return self._summary(category, 0)

    def update(self, category_id: UUID, payload: UpdateCategoryRequest) -> CategorySummary:
        category = self._require(category_id)

        # Checked before anything else changes, so a request that also renames
        # the category is rejected whole rather than half-applied.
        if payload.slug is not None and payload.slug.strip().lower() != category.slug:
            raise ValidationError(
                "A category slug cannot be changed. Every document already "
                "classified as this category, and every vector in Qdrant, stores "
                f"'{category.slug}'; renaming it would orphan them silently. "
                "Create a new category and deactivate this one instead.",
                error_code=ErrorCode.CATEGORY_SLUG_IMMUTABLE,
            )

        changed: list[str] = []

        if payload.name is not None and payload.name != category.name:
            if self.categories.name_taken(payload.name, excluding=category.id):
                raise ConflictError(
                    f"A category named '{payload.name}' already exists.",
                    error_code=ErrorCode.CATEGORY_SLUG_TAKEN,
                )
            category.name = payload.name
            changed.append("name")

        if payload.description is not None and payload.description != category.description:
            # The classifier reads this, so an edit changes how future documents
            # are labelled. Worth naming in the audit trail as its own field.
            category.description = payload.description
            changed.append("description")

        if payload.sort_order is not None and payload.sort_order != category.sort_order:
            category.sort_order = payload.sort_order
            changed.append("sort_order")

        if payload.is_active is not None and payload.is_active != category.is_active:
            category.is_active = payload.is_active
            changed.append("is_active")

        if changed:
            self.db.flush()
            action = (
                CATEGORY_DEACTIVATED
                if payload.is_active is False and "is_active" in changed
                else CATEGORY_UPDATED
            )
            self._audit(action, category, {"fields": sorted(changed)})
            self.db.commit()
            self.db.refresh(category)

        counts = self.categories.document_counts()
        return self._summary(category, counts.get(category.id, 0))

    def delete(self, category_id: UUID) -> DeleteCategoryResponse:
        """Hard delete when unused; deactivate when not.

        Returning 200 either way is deliberate. The Super Admin asked for the
        category to stop being available, and it has - refusing with a 409 would
        make them hunt for a second button that does the survivable thing.
        """
        category = self._require(category_id)
        in_use = self.categories.document_counts().get(category.id, 0)

        if in_use:
            already_inactive = not category.is_active
            category.is_active = False
            self.db.flush()
            if not already_inactive:
                self._audit(CATEGORY_DEACTIVATED, category,
                            {"reason": "in_use", "document_count": in_use})
            self.db.commit()
            logger.info("Category deactivated instead of deleted",
                        extra={"category_id": str(category.id), "documents": in_use})
            return DeleteCategoryResponse(
                deleted=False, deactivated=True, document_count=in_use
            )

        # Audited before the row goes, or the entity_id would be dangling by the
        # time the audit is written.
        self._audit(CATEGORY_DELETED, category, {"slug": category.slug})
        self.categories.delete(category)
        self.db.commit()

        logger.info("Category deleted", extra={"category_id": str(category_id)})
        return DeleteCategoryResponse(deleted=True, deactivated=False, document_count=0)

    # --- Internals -------------------------------------------------------- #

    def _require(self, category_id: UUID) -> DocumentCategory:
        category = self.categories.get(category_id)
        if category is None:
            raise NotFoundError(
                "That category does not exist.",
                error_code=ErrorCode.CATEGORY_NOT_FOUND,
            )
        return category

    def _summary(self, category: DocumentCategory, document_count: int) -> CategorySummary:
        return CategorySummary(
            id=category.id,
            slug=category.slug,
            name=category.name,
            description=category.description,
            sort_order=category.sort_order,
            is_active=category.is_active,
            document_count=document_count,
            created_at=category.created_at,
            updated_at=category.updated_at,
        )

    def _audit(self, action: str, category: DocumentCategory, metadata: dict) -> None:
        # organization_id stays NULL: the taxonomy belongs to no tenant, and
        # attributing this to one would make the audit trail lie.
        self.audit.record(
            action=action,
            entity_type="document_category",
            entity_id=category.id,
            user_id=self.super_admin_id,
            actor_type=ActorType.SUPER_ADMIN,
            metadata=metadata,
        )
