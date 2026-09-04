"""Database access for Organization Admins and OTP requests."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.auth.repositories.user_repository import coerce_ip
from app.modules.organizations.models import (
    IdentifierType,
    OrganizationAdmin,
    OtpRequest,
)


def normalise_identifier(raw: str) -> tuple[str, IdentifierType]:
    """Canonical form plus which kind it is.

    Normalisation happens before lookup so that ``Admin@Acme.example`` and
    ``+91 98765 43210`` resolve the same way they were stored. A mismatch here
    would present as "the OTP does not work" with no visible cause.
    """
    value = (raw or "").strip()
    if "@" in value:
        return value.lower(), IdentifierType.EMAIL
    return re.sub(r"[^\d+]", "", value), IdentifierType.MOBILE


class OrganizationAdminRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        organization_id: UUID,
        full_name: str,
        email: str,
        mobile: str | None = None,
    ) -> OrganizationAdmin:
        admin = OrganizationAdmin(
            organization_id=organization_id,
            full_name=full_name,
            email=email.strip().lower(),
            mobile=normalise_identifier(mobile)[0] if mobile else None,
        )
        self.db.add(admin)
        self.db.flush()
        return admin

    def get(self, admin_id: UUID) -> OrganizationAdmin | None:
        return self.db.get(OrganizationAdmin, admin_id)

    def get_by_identifier(self, identifier: str) -> OrganizationAdmin | None:
        """Resolve an email or mobile to exactly one admin.

        Identifiers are globally unique, not unique per organization — an
        ambiguous lookup here would be a cross-tenant login bug.
        """
        value, kind = normalise_identifier(identifier)
        column = OrganizationAdmin.email if kind is IdentifierType.EMAIL else OrganizationAdmin.mobile
        return self.db.execute(
            select(OrganizationAdmin).where(column == value)
        ).scalar_one_or_none()

    def identifier_taken(self, *, email: str, mobile: str | None,
                         exclude_id: UUID | None = None) -> bool:
        conditions = [OrganizationAdmin.email == email.strip().lower()]
        if mobile:
            conditions.append(OrganizationAdmin.mobile == normalise_identifier(mobile)[0])

        stmt = select(func.count()).select_from(OrganizationAdmin).where(or_(*conditions))
        if exclude_id is not None:
            stmt = stmt.where(OrganizationAdmin.id != exclude_id)
        return self.db.execute(stmt).scalar_one() > 0

    def list_for_organization(self, organization_id: UUID) -> list[OrganizationAdmin]:
        return list(
            self.db.execute(
                select(OrganizationAdmin)
                .where(OrganizationAdmin.organization_id == organization_id)
                .order_by(OrganizationAdmin.created_at.asc())
            ).unique().scalars()
        )

    def touch_login(self, admin: OrganizationAdmin) -> None:
        admin.last_login_at = datetime.now(timezone.utc)
        self.db.flush()


class OtpRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        identifier: str,
        identifier_type: IdentifierType,
        otp_hash: str,
        expires_at: datetime,
        organization_admin_id: UUID | None,
        ip_address: str | None = None,
    ) -> OtpRequest:
        request = OtpRequest(
            identifier=identifier,
            identifier_type=identifier_type,
            otp_hash=otp_hash,
            expires_at=expires_at,
            organization_admin_id=organization_admin_id,
            # otp_requests.ip_address is INET. The value comes from
            # X-Forwarded-For or the transport, so it is attacker-influenced: a
            # non-IP string would raise on insert and turn a login attempt into
            # a 500. Validated, and a bad one dropped rather than failing.
            ip_address=coerce_ip(ip_address),
        )
        self.db.add(request)
        self.db.flush()
        return request

    def newest_pending(self, identifier: str) -> OtpRequest | None:
        """The most recent unverified, unexpired request for an identifier.

        Newest wins: requesting a second code invalidates the first in practice,
        without a separate expiry sweep.
        """
        return self.db.execute(
            select(OtpRequest)
            .where(OtpRequest.identifier == identifier)
            .where(OtpRequest.is_verified.is_(False))
            .where(OtpRequest.expires_at > datetime.now(timezone.utc))
            .order_by(OtpRequest.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def count_recent(self, identifier: str, *, since: datetime) -> int:
        return self.db.execute(
            select(func.count())
            .select_from(OtpRequest)
            .where(OtpRequest.identifier == identifier)
            .where(OtpRequest.created_at >= since)
        ).scalar_one()
