"""Translates OTP requests into service calls (spec §12)."""

from __future__ import annotations

from fastapi import Request, Response
from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token
from app.modules.organizations.schemas.otp import (
    OrganizationRef,
    OtpRequestPayload,
    OtpRequestResponse,
    OtpTokenResponse,
    OtpVerifyPayload,
)
from app.modules.organizations.services.otp_service import OtpService
from config.settings import settings

REFRESH_COOKIE = "erag_refresh"


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


class OtpController:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.service = OtpService(db)

    def request_code(self, payload: OtpRequestPayload, request: Request) -> dict:
        issue = self.service.request(payload.identifier, ip_address=client_ip(request))
        return {
            "success": True,
            # Deliberately conditional: identical whether or not the identifier
            # exists, so the response cannot be used to enumerate accounts.
            "message": "If that identifier exists, a code has been sent.",
            "data": OtpRequestResponse(
                expires_in_seconds=issue.expires_in_seconds,
                development_code=issue.development_code,
            ).model_dump(mode="json"),
        }

    def verify_code(
        self, payload: OtpVerifyPayload, request: Request, response: Response
    ) -> dict:
        admin = self.service.verify(payload.identifier, payload.otp).admin
        organization = admin.organization

        access = create_access_token(
            str(admin.id),
            subject_type="ORG_ADMIN",
            organization_id=str(organization.id),
        )
        refresh, _jti = create_refresh_token(
            str(admin.id),
            subject_type="ORG_ADMIN",
            organization_id=str(organization.id),
        )

        response.set_cookie(
            REFRESH_COOKIE,
            refresh,
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=settings.REFRESH_TOKEN_DAYS * 86_400,
            path="/",
        )

        return {
            "success": True,
            "data": OtpTokenResponse(
                access_token=access,
                organization=OrganizationRef(
                    id=organization.id, name=organization.name, slug=organization.slug
                ),
                full_name=admin.full_name,
            ).model_dump(mode="json"),
        }
