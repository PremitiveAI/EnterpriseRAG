"""Exception handlers.

One envelope for every failure, and no stack trace ever reaches a client (§38).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _envelope(
    request: Request, status: int, code: str, message: str, details: dict | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error_code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error(exc.message, exc_info=exc, extra={"error_code": str(exc.error_code)})
        else:
            logger.info(exc.message, extra={"error_code": str(exc.error_code)})
        return _envelope(request, exc.status_code, str(exc.error_code), exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(p) for p in err["loc"][1:]), "issue": err["msg"]}
            for err in exc.errors()
        ]
        return _envelope(
            request,
            422,
            str(ErrorCode.VALIDATION_ERROR),
            "The request failed validation.",
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.UNAUTHORIZED if exc.status_code == 401 else ErrorCode.INTERNAL_ERROR
        if exc.status_code == 404:
            message = "Not found."
            code = ErrorCode.DOCUMENT_NOT_FOUND if "document" in request.url.path else code
        else:
            message = str(exc.detail)
        return _envelope(request, exc.status_code, str(code), message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Full trace to the log, a generic message to the client.
        logger.error("Unhandled exception", exc_info=exc, extra={"path": request.url.path})
        return _envelope(
            request, 500, str(ErrorCode.INTERNAL_ERROR), "An unexpected error occurred."
        )
