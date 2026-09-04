"""Application exceptions.

Every failure raised by a service carries an ``ErrorCode`` and an HTTP status.
Stack traces are logged server-side and never returned to a client (§38).
"""

from typing import Any

from app.core.error_codes import ErrorCode


class AppError(Exception):
    """Base for all handled application errors."""

    status_code: int = 500
    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        error_code: ErrorCode | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.error_code = error_code or self.error_code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        super().__init__(self.message)


# --- 4xx ---------------------------------------------------------------- #


class ValidationError(AppError):
    status_code = 422
    error_code = ErrorCode.VALIDATION_ERROR
    message = "The request failed validation."


class UnauthorizedError(AppError):
    status_code = 401
    error_code = ErrorCode.UNAUTHORIZED
    message = "Authentication required."


class InvalidCredentialsError(AppError):
    status_code = 401
    error_code = ErrorCode.INVALID_CREDENTIALS
    # Deliberately identical for unknown email and wrong password, so the
    # response cannot be used to enumerate accounts.
    message = "Invalid email or password."


class AccountInactiveError(AppError):
    status_code = 403
    error_code = ErrorCode.ACCOUNT_INACTIVE
    message = "This account is inactive."


class TokenExpiredError(AppError):
    status_code = 401
    error_code = ErrorCode.TOKEN_EXPIRED
    message = "Access token has expired."


class RefreshTokenInvalidError(AppError):
    status_code = 401
    error_code = ErrorCode.REFRESH_TOKEN_INVALID
    message = "Refresh token is invalid or has been revoked."


class ForbiddenError(AppError):
    status_code = 403
    error_code = ErrorCode.FORBIDDEN
    message = "You do not have access to this resource."


class NotFoundError(AppError):
    status_code = 404
    message = "Resource not found."


class ConflictError(AppError):
    status_code = 409
    message = "The request conflicts with the current state."


class RateLimitError(AppError):
    status_code = 429
    error_code = ErrorCode.RATE_LIMIT_EXCEEDED
    message = "Too many requests. Try again shortly."


# --- 5xx ---------------------------------------------------------------- #


class ServiceUnavailableError(AppError):
    status_code = 503
    error_code = ErrorCode.SERVICE_UNAVAILABLE
    message = "A required service is unavailable."


class NoActiveLLMProviderError(AppError):
    """No provider row is active (ADR-010).

    A 503 and not a 500: the system is working correctly and is telling the
    truth about its configuration. Falling back to a templated answer here would
    hide the one thing a Super Admin needs to see.
    """

    status_code = 503
    error_code = ErrorCode.LLM_NO_ACTIVE_PROVIDER
    message = "No LLM provider is active. Register one in Super Admin settings."


class LLMConfigUnavailableError(AppError):
    """The active configuration could not be read or decrypted (ADR-010 §5).

    Raised only after last-known-good has been exhausted - a cold process, or a
    cached configuration older than the staleness ceiling. Never carries the
    state of the key registry to the client.
    """

    status_code = 503
    error_code = ErrorCode.LLM_CONFIG_UNAVAILABLE
    message = "The LLM configuration is temporarily unavailable."


class LLMProviderNotFoundError(AppError):
    status_code = 404
    error_code = ErrorCode.LLM_PROVIDER_NOT_FOUND
    message = "That provider does not exist."


class LLMProviderTestFailedError(AppError):
    """The pre-flight call did not succeed, so nothing was written.

    Deliberately 422 and not 502: from the caller's point of view the
    *submission* is what failed - these credentials, this model, this endpoint.
    """

    status_code = 422
    error_code = ErrorCode.LLM_PROVIDER_TEST_FAILED
    message = "The provider did not accept this configuration."


class LLMProviderChangedError(AppError):
    """Someone edited the credential between the test and the commit.

    Activating anyway would put a configuration live that nothing has verified.
    """

    status_code = 409
    error_code = ErrorCode.LLM_PROVIDER_CHANGED
    message = (
        "This provider was changed while it was being verified. "
        "Review it and try again."
    )


class LLMProviderActiveError(AppError):
    status_code = 409
    error_code = ErrorCode.LLM_PROVIDER_ACTIVE
    message = "The active provider cannot be deleted. Activate another one first."


class StorageError(AppError):
    status_code = 500
    error_code = ErrorCode.STORAGE_FILE_MISSING
    message = "The stored file could not be read."


class IllegalTransition(AppError):
    """Raised by the document state machine (docs/celery/state-machine.md).

    Raising loudly is deliberate: a bug that would silently resurrect a deleted
    document instead fails in a test.
    """

    status_code = 409
    error_code = ErrorCode.PROCESSING_FAILED
    message = "Illegal status transition."
