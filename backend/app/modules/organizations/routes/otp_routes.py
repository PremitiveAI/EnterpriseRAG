"""Organization Admin OTP login. Path definitions and wiring only (spec §12)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.organizations.controllers.otp_controller import OtpController
from app.modules.organizations.schemas.otp import OtpRequestPayload, OtpVerifyPayload

router = APIRouter(prefix="/auth/otp", tags=["auth"])


@router.post("/request", response_model=None)
def request_otp(
    payload: OtpRequestPayload, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Issue a code. Public - the caller has no token yet, by definition."""
    return OtpController(db).request_code(payload, request)


@router.post("/verify", response_model=None)
def verify_otp(
    payload: OtpVerifyPayload,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    return OtpController(db).verify_code(payload, request, response)
