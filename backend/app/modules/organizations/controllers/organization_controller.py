"""Translates Super Admin organization requests into service calls (spec §12)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.organizations.schemas.organization import (
    CreateAdminRequest,
    CreateOrganizationRequest,
    UpdateAdminRequest,
    UpdateOrganizationRequest,
)
from app.modules.organizations.services.organization_service import OrganizationService


class OrganizationController:
    def __init__(self, db: Session, super_admin_id: UUID) -> None:
        self.service = OrganizationService(db, super_admin_id)

    # --- Organizations -------------------------------------------------- #

    def create(self, payload: CreateOrganizationRequest) -> dict:
        detail = self.service.create(payload)
        return {"success": True, "message": "Organization created.",
                "data": detail.model_dump(mode="json")}

    def list(self, *, search: str | None, status: str | None,
             page: int, page_size: int) -> dict:
        result = self.service.list(
            search=search or None, status=status or None,
            page=max(1, page), page_size=min(max(1, page_size), 100),
        )
        return {"success": True, "data": result.model_dump(mode="json")}

    def detail(self, organization_id: UUID) -> dict:
        return {"success": True,
                "data": self.service.detail(organization_id).model_dump(mode="json")}

    def update(self, organization_id: UUID, payload: UpdateOrganizationRequest) -> dict:
        detail = self.service.update(organization_id, payload)
        return {"success": True, "message": "Organization updated.",
                "data": detail.model_dump(mode="json")}

    def suspend(self, organization_id: UUID) -> dict:
        detail = self.service.set_status(organization_id, active=False)
        return {"success": True, "message": "Organization suspended.",
                "data": detail.model_dump(mode="json")}

    def activate(self, organization_id: UUID) -> dict:
        detail = self.service.set_status(organization_id, active=True)
        return {"success": True, "message": "Organization activated.",
                "data": detail.model_dump(mode="json")}

    def delete(self, organization_id: UUID, *, force: bool) -> dict:
        data = self.service.delete(organization_id, force=force)
        return {"success": True, "message": "Organization deleted.", "data": data}

    # --- Admins ---------------------------------------------------------- #

    def create_admin(self, organization_id: UUID, payload: CreateAdminRequest) -> dict:
        admin = self.service.create_admin(organization_id, payload)
        return {"success": True, "message": "Organization admin created.",
                "data": admin.model_dump(mode="json")}

    def list_admins(self, organization_id: UUID) -> dict:
        admins = self.service.list_admins(organization_id)
        return {"success": True,
                "data": {"items": [a.model_dump(mode="json") for a in admins]}}

    def update_admin(self, admin_id: UUID, payload: UpdateAdminRequest) -> dict:
        admin = self.service.update_admin(admin_id, payload)
        return {"success": True, "message": "Organization admin updated.",
                "data": admin.model_dump(mode="json")}

    def deactivate_admin(self, admin_id: UUID) -> dict:
        data = self.service.deactivate_admin(admin_id)
        return {"success": True, "message": "Organization admin deactivated.",
                "data": data}
