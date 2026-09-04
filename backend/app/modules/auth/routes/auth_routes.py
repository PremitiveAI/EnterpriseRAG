"""Auth routes. Path definitions and dependency wiring only (spec §12)."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user
from app.modules.auth.controllers.auth_controller import AuthController
from app.modules.auth.models import User
from app.modules.auth.schemas.auth import LoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "erag_refresh"


@router.post("/login", response_model=None, status_code=status.HTTP_200_OK)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    return AuthController(db).login(payload, request, response)


@router.post("/refresh", response_model=None)
def refresh(
    request: Request,
    erag_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: Session = Depends(get_db),
) -> dict:
    return AuthController(db).refresh(erag_refresh, request)


@router.post("/logout", response_model=None)
def logout(
    request: Request,
    response: Response,
    erag_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: Session = Depends(get_db),
) -> dict:
    return AuthController(db).logout(erag_refresh, request, response)


@router.get("/me", response_model=None)
def me(user: User = Depends(current_user)) -> dict:
    return {"success": True, "data": UserResponse.model_validate(user).model_dump(mode="json")}


__all__ = ["router", "REFRESH_COOKIE", "TokenResponse"]
