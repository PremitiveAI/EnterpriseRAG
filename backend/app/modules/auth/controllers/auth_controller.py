"""Translates HTTP requests into AuthService calls and back (spec §12)."""

from __future__ import annotations

from fastapi import Request, Response
from sqlalchemy.orm import Session

from app.core.exceptions import RefreshTokenInvalidError
from app.modules.auth.schemas.auth import LoginRequest, TokenResponse, UserResponse
from app.modules.auth.services.auth_service import AuthService
from config.settings import settings

REFRESH_COOKIE = "erag_refresh"


def _client_ip(request: Request) -> str | None:
    # X-Forwarded-For is only trustworthy behind a proxy that sets it; in
    # development it is absent and request.client is used.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


class AuthController:
    def __init__(self, db: Session) -> None:
        self.service = AuthService(db)

    def login(self, payload: LoginRequest, request: Request, response: Response) -> dict:
        user, access, refresh, _jti = self.service.authenticate(
            payload.email,
            payload.password,
            request_id=getattr(request.state, "request_id", None),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )

        self._set_refresh_cookie(response, refresh)

        body = TokenResponse(
            access_token=access,
            expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
            user=UserResponse.model_validate(user),
        )
        return {"success": True, "data": body.model_dump(mode="json")}

    def refresh(self, refresh_token: str | None, request: Request) -> dict:
        if not refresh_token:
            raise RefreshTokenInvalidError("No refresh token supplied.")

        user, access = self.service.refresh_access_token(refresh_token)
        body = TokenResponse(
            access_token=access,
            expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
            user=UserResponse.model_validate(user),
        )
        return {"success": True, "data": body.model_dump(mode="json")}

    def logout(self, refresh_token: str | None, request: Request, response: Response) -> dict:
        self.service.logout(
            refresh_token,
            user_id=getattr(request.state, "user_id", None),
            request_id=getattr(request.state, "request_id", None),
        )
        response.delete_cookie(REFRESH_COOKIE, path="/")
        return {"success": True, "message": "Signed out."}

    @staticmethod
    def _set_refresh_cookie(response: Response, refresh: str) -> None:
        response.set_cookie(
            key=REFRESH_COOKIE,
            value=refresh,
            max_age=settings.REFRESH_TOKEN_DAYS * 86_400,
            httponly=True,
            # Secure is required in production; localhost development is HTTP.
            secure=settings.is_production,
            samesite="strict",
            path="/",
        )
