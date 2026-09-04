"""Super Admin organization management (docs/release-2/features/organization-management.md).

Business rules live here. The controller translates HTTP into these calls and
nothing else (§12).

Two rules carry the Super Admin privacy guarantee:

* No method on this service returns document titles, filenames, chat content or
  anything else an organization owns - only counts and configuration.
* Changing an admin's email or mobile writes its own audit action, because a
  Super Admin who repoints an identifier can receive that admin's OTP. Access
  control cannot prevent it; visibility is what stops it being quiet.
"""

from __future__ import annotations

import secrets
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.rate_limit import parse_rule
from app.modules.auth.models import ActorType
from app.modules.auth.repositories.user_repository import AuditRepository
from app.modules.organizations.models import (
    Organization,
    OrganizationAdmin,
    OrganizationStatus,
)
from app.modules.organizations.repositories.admin_repository import (
    OrganizationAdminRepository,
    normalise_identifier,
)
from app.modules.organizations.repositories.organization_repository import (
    OrganizationRepository,
)
from app.modules.organizations.schemas.organization import (
    AdminSummary,
    CreateAdminRequest,
    CreateOrganizationRequest,
    OrganizationCounts,
    OrganizationDetail,
    OrganizationLimits,
    OrganizationListResponse,
    OrganizationSummary,
    UpdateAdminRequest,
    UpdateOrganizationRequest,
)
from app.vector import client as vector_client

logger = get_logger(__name__)

# Audit actions specific to Release 2.
ORG_CREATED = "organization.created"
ORG_UPDATED = "organization.updated"
ORG_SUSPENDED = "organization.suspended"
ORG_ACTIVATED = "organization.activated"
ORG_DELETED = "organization.deleted"
ADMIN_CREATED = "organization_admin.created"
ADMIN_UPDATED = "organization_admin.updated"
ADMIN_CONTACT_CHANGED = "organization_admin.contact_changed"
ADMIN_DEACTIVATED = "organization_admin.deactivated"


class OrganizationService:
    def __init__(self, db: Session, super_admin_id: UUID) -> None:
        self.db = db
        self.super_admin_id = super_admin_id
        self.organizations = OrganizationRepository(db)
        self.admins = OrganizationAdminRepository(db)
        self.audit = AuditRepository(db)

    # --- Organizations -------------------------------------------------- #

    def create(self, payload: CreateOrganizationRequest) -> OrganizationDetail:
        if self.organizations.get_by_slug(payload.slug) is not None:
            raise ConflictError(
                "That slug is already in use.",
                error_code=ErrorCode.CONFLICT,
                details={"slug": payload.slug},
            )

        organization = self.organizations.create(
            name=payload.name,
            slug=payload.slug,
            public_chat_key=secrets.token_urlsafe(32),
            contact_email=payload.contact_email,
            max_documents=payload.max_documents,
            max_document_size_mb=payload.max_document_size_mb,
            public_chat_enabled=payload.public_chat_enabled,
        )
        self.db.flush()

        # Provisioning is part of creation. An organization that exists in
        # PostgreSQL with no collection would accept uploads that then fail at
        # the indexing stage with no obvious cause, so the row is rolled back
        # rather than left half-created.
        try:
            vector_client.ensure_collection(organization.id)
        except Exception as exc:
            self.db.rollback()
            logger.exception("Could not provision the collection")
            raise ValidationError(
                "The vector store is unavailable, so the organization was not created.",
                error_code=ErrorCode.VECTOR_INDEXING_FAILED,
                status_code=503,
            ) from exc

        self._audit(ORG_CREATED, organization, {"slug": organization.slug})
        self.db.commit()

        logger.info("Organization created",
                    extra={"organization_id": str(organization.id)})
        return self._detail(organization)

    def list(
        self, *, search: str | None, status: str | None, page: int, page_size: int
    ) -> OrganizationListResponse:
        parsed_status = None
        if status:
            try:
                parsed_status = OrganizationStatus(status.strip().upper())
            except ValueError as exc:
                raise ValidationError(
                    f"Unknown status '{status}'.",
                    details={"allowed": [str(s) for s in OrganizationStatus]},
                ) from exc

        items, total = self.organizations.list(
            search=search, status=parsed_status, page=page, page_size=page_size
        )
        total_pages = (total + page_size - 1) // page_size if total else 0

        return OrganizationListResponse(
            items=[self._summary(o) for o in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    def detail(self, organization_id: UUID) -> OrganizationDetail:
        return self._detail(self._require(organization_id))

    def update(
        self, organization_id: UUID, payload: UpdateOrganizationRequest
    ) -> OrganizationDetail:
        organization = self._require(organization_id)
        changed: dict[str, object] = {}

        for field in ("name", "contact_email", "max_documents", "max_document_size_mb",
                      "public_chat_enabled", "public_chat_greeting"):
            if field in payload.model_fields_set:
                value = getattr(payload, field)
                if getattr(organization, field) != value:
                    setattr(organization, field, value)
                    changed[field] = value

        # Rate limits are validated at SAVE time, not at request time. A
        # mistyped limit must not silently become no limit at all - the same
        # reasoning as `parse_rule` rejecting a bare count.
        for field in ("rate_limit_chat", "rate_limit_upload", "rate_limit_public_chat"):
            if field not in payload.model_fields_set:
                continue
            value = getattr(payload, field)
            if value:
                try:
                    parse_rule(value, scope="user")
                except ValueError as exc:
                    raise ValidationError(
                        f"{field} must look like '30/minute'.",
                        details={"value": value, "reason": str(exc)},
                    ) from exc
            setattr(organization, field, value)
            changed[field] = value

        if changed:
            self._audit(ORG_UPDATED, organization, {"fields": sorted(changed)})
            self.db.commit()
            self.db.refresh(organization)

        return self._detail(organization)

    def set_status(self, organization_id: UUID, *, active: bool) -> OrganizationDetail:
        organization = self._require(organization_id)
        organization.status = (
            OrganizationStatus.ACTIVE if active else OrganizationStatus.SUSPENDED
        )
        self.db.flush()

        self._audit(ORG_ACTIVATED if active else ORG_SUSPENDED, organization, {})
        self.db.commit()

        # Suspension takes effect on the next request because the organization
        # is re-checked per request, not only at login.
        logger.info("Organization status changed",
                    extra={"organization_id": str(organization.id),
                           "status": str(organization.status)})
        return self._detail(organization)

    def delete(self, organization_id: UUID, *, force: bool = False) -> dict[str, object]:
        organization = self._require(organization_id)

        if not force and self.organizations.has_documents(organization.id):
            raise ConflictError(
                "This organization still has documents. Suspend it instead, "
                "or pass force to delete it and its data.",
                error_code=ErrorCode.CONFLICT,
                details={"documents": self.organizations.document_count(organization.id)},
            )

        self.organizations.soft_delete(organization)
        self._audit(ORG_DELETED, organization, {"slug": organization.slug})
        self.db.commit()

        # Database first, vectors second - the same ordering as document
        # deletion. If this fails the organization is already unreachable
        # (login is refused), so the failure degrades safely.
        vectors_dropped = True
        try:
            vector_client.delete_collection(organization.id)
        except Exception:
            vectors_dropped = False
            logger.exception("Collection not dropped",
                             extra={"organization_id": str(organization.id)})

        return {
            "organization_id": str(organization.id),
            "status": str(OrganizationStatus.DELETED),
            "vectors_dropped": vectors_dropped,
        }

    # --- Organization admins -------------------------------------------- #

    def create_admin(
        self, organization_id: UUID, payload: CreateAdminRequest
    ) -> AdminSummary:
        organization = self._require(organization_id)
        if not organization.is_active:
            raise ConflictError(
                "This organization is not active.",
                error_code=ErrorCode.CONFLICT,
            )

        if self.admins.identifier_taken(email=payload.email, mobile=payload.mobile):
            # Globally unique, not unique per organization: an ambiguous OTP
            # lookup would be a cross-tenant login bug.
            raise ConflictError(
                "That email or mobile number is already in use.",
                error_code=ErrorCode.CONFLICT,
            )

        admin = self.admins.create(
            organization_id=organization.id,
            full_name=payload.full_name,
            email=payload.email,
            mobile=payload.mobile,
        )
        self._audit(ADMIN_CREATED, organization, {"admin_id": str(admin.id)},
                    entity_type="organization_admin", entity_id=admin.id)
        self.db.commit()
        return self._admin_summary(admin)

    def list_admins(self, organization_id: UUID) -> list[AdminSummary]:
        self._require(organization_id)
        return [
            self._admin_summary(a)
            for a in self.admins.list_for_organization(organization_id)
        ]

    def update_admin(self, admin_id: UUID, payload: UpdateAdminRequest) -> AdminSummary:
        admin = self.admins.get(admin_id)
        if admin is None:
            raise NotFoundError("No admin with that id.")

        contact_changed: dict[str, object] = {}

        if payload.full_name is not None:
            admin.full_name = payload.full_name.strip()

        for field in ("email", "mobile"):
            if field not in payload.model_fields_set:
                continue
            raw = getattr(payload, field)
            value = normalise_identifier(raw)[0] if raw else None
            current = getattr(admin, field)
            if value == current:
                continue

            if value and self.admins.identifier_taken(
                email=value if field == "email" else admin.email,
                mobile=value if field == "mobile" else admin.mobile,
                exclude_id=admin.id,
            ):
                raise ConflictError("That email or mobile number is already in use.",
                                    error_code=ErrorCode.CONFLICT)

            contact_changed[field] = {"from": current, "to": value}
            setattr(admin, field, value)

        self.db.flush()

        if contact_changed:
            # 🔴 Its own action, deliberately. A Super Admin who repoints an
            # admin's email can receive that admin's OTP and enter the
            # organization. Access control cannot prevent that - it is a
            # legitimate Super Admin power - so the guarantee is "cannot read
            # SILENTLY", and this row is what makes that true.
            self._audit(
                ADMIN_CONTACT_CHANGED,
                admin.organization,
                {"changes": contact_changed},
                entity_type="organization_admin",
                entity_id=admin.id,
            )
        else:
            self._audit(ADMIN_UPDATED, admin.organization, {},
                        entity_type="organization_admin", entity_id=admin.id)

        self.db.commit()
        return self._admin_summary(admin)

    def deactivate_admin(self, admin_id: UUID) -> dict[str, object]:
        admin = self.admins.get(admin_id)
        if admin is None:
            raise NotFoundError("No admin with that id.")

        admin.is_active = False
        self.db.flush()
        self._audit(ADMIN_DEACTIVATED, admin.organization, {},
                    entity_type="organization_admin", entity_id=admin.id)
        self.db.commit()

        # Their token fails at the next request: resolve_principal re-checks
        # is_active every time rather than trusting the claim.
        return {"admin_id": str(admin.id), "is_active": False}

    # --- Internals ------------------------------------------------------- #

    def _require(self, organization_id: UUID) -> Organization:
        organization = self.organizations.get(organization_id)
        if organization is None:
            raise NotFoundError("No organization with that id.")
        return organization

    def _summary(self, organization: Organization) -> OrganizationSummary:
        return OrganizationSummary(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            status=str(organization.status),
            contact_email=organization.contact_email,
            public_chat_enabled=organization.public_chat_enabled,
            counts=OrganizationCounts(**self.organizations.counts_for(organization.id)),
            created_at=organization.created_at,
        )

    def _detail(self, organization: Organization) -> OrganizationDetail:
        return OrganizationDetail(
            **self._summary(organization).model_dump(),
            limits=OrganizationLimits(
                max_documents=organization.max_documents,
                max_document_size_mb=organization.max_document_size_mb,
                rate_limit_chat=organization.rate_limit_chat,
                rate_limit_upload=organization.rate_limit_upload,
                rate_limit_public_chat=organization.rate_limit_public_chat,
            ),
            public_chat_key=organization.public_chat_key,
            public_chat_greeting=organization.public_chat_greeting,
            updated_at=organization.updated_at,
        )

    @staticmethod
    def _admin_summary(admin: OrganizationAdmin) -> AdminSummary:
        return AdminSummary(
            id=admin.id,
            full_name=admin.full_name,
            email=admin.email,
            mobile=admin.mobile,
            is_active=admin.is_active,
            last_login_at=admin.last_login_at,
            created_at=admin.created_at,
        )

    def _audit(self, action: str, organization: Organization, metadata: dict,
               *, entity_type: str = "organization", entity_id: UUID | None = None) -> None:
        self.audit.record(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id or organization.id,
            user_id=self.super_admin_id,
            actor_type=ActorType.SUPER_ADMIN,
            organization_id=organization.id,
            metadata=metadata,
        )
