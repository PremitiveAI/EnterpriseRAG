"""OTP request and verification contracts."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class OtpRequestPayload(BaseModel):
    """An email address or a mobile number. One field, because the user picks."""

    identifier: str = Field(min_length=3, max_length=255)


class OtpRequestResponse(BaseModel):
    expires_in_seconds: int
    # Present outside production only, so the login screen can show the fixed
    # development code rather than the admin guessing at it.
    development_code: str | None = None


class OtpVerifyPayload(BaseModel):
    identifier: str = Field(min_length=3, max_length=255)
    otp: str = Field(min_length=4, max_length=8)


class OrganizationRef(BaseModel):
    id: UUID
    name: str
    slug: str


class OtpTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    organization: OrganizationRef
    full_name: str
