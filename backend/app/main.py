"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models_registry  # noqa: F401  # every model, before any flush
from app.cache import redis_client
from app.core.logging import configure_logging, get_logger
from app.middlewares.auth import AuthMiddleware
from app.middlewares.error_handler import register_exception_handlers
from app.middlewares.rate_limit import RateLimitMiddleware
from app.middlewares.request_id import RequestIDMiddleware
from app.middlewares.security_headers import SecurityHeadersMiddleware
from app.modules.auth.routes import auth_routes
from app.modules.chat.routes import chat_routes, public_routes
from app.modules.ai.routes import agent_rule_routes, llm_provider_routes
from app.modules.documents.routes import category_routes, document_routes
from app.modules.organizations.routes import organization_routes, otp_routes
from config.settings import settings

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "Starting %s", settings.APP_NAME, extra={"environment": settings.ENVIRONMENT}
    )

    settings.STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

    # Dependency checks are warnings, not failures: the API must still start so
    # /health can report what is wrong.
    if not redis_client.ping():
        logger.warning("Redis is not reachable — Celery and chat context will not work")

    # Release 2: collections are per-organization and provisioned when an
    # organization is created, so there is no global collection to warm here.
    # Reachability is still worth reporting at startup.
    try:
        from app.vector.client import is_available

        if not is_available():
            logger.warning("Qdrant is not reachable — retrieval and chat will not work")
    except Exception as exc:
        logger.warning("Qdrant check failed",
                       extra={"error_type": type(exc).__name__})

    # A warning, not a failure, and the split is deliberate (ADR-010). The key
    # REGISTRY is pure configuration and hard-fails in Settings, before this
    # runs. Checking that every stored row's key id resolves needs a query, so
    # it belongs with the dependency checks above: a database that is not up
    # yet must not stop the API from starting and reporting that.
    _warn_about_unreadable_credentials()

    yield

    logger.info("Shutting down %s", settings.APP_NAME)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.2.0",
        description="Enterprise AI document management and RAG platform.",
        lifespan=lifespan,
        # Interactive docs are disabled in production (spec §40).
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Middleware runs bottom-up: the LAST added is the outermost. So the order
    # a request actually travels is the reverse of the order written here:
    #
    #   SecurityHeaders → RequestID → CORS → Auth → RateLimit → route
    #
    # RateLimit sits inside Auth deliberately: `request.state.user_id` is set by
    # then, so a per-user limit keys on the real identity, and an
    # unauthenticated caller gets 401 rather than a confusing 429.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,  # never "*"
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIDMiddleware)
    # Outermost, so the headers are present even on a response produced by a
    # middleware below it — a 401 or a 429 included.
    app.add_middleware(SecurityHeadersMiddleware)

    register_exception_handlers(app)

    app.include_router(auth_routes.router, prefix=settings.API_V1_PREFIX)
    app.include_router(document_routes.router, prefix=settings.API_V1_PREFIX)
    app.include_router(document_routes.config_router, prefix=settings.API_V1_PREFIX)
    app.include_router(chat_routes.router, prefix=settings.API_V1_PREFIX)
    app.include_router(public_routes.router, prefix=settings.API_V1_PREFIX)
    app.include_router(otp_routes.router, prefix=settings.API_V1_PREFIX)
    app.include_router(organization_routes.router, prefix=settings.API_V1_PREFIX)
    app.include_router(category_routes.router, prefix=settings.API_V1_PREFIX)
    app.include_router(agent_rule_routes.router, prefix=settings.API_V1_PREFIX)
    app.include_router(llm_provider_routes.router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {
            "success": True,
            "data": {
                "status": "ok",
                "app": settings.APP_NAME,
                "environment": settings.ENVIRONMENT,
                "redis": "up" if redis_client.ping() else "down",
                "qdrant": "up" if _qdrant_up() else "down",
                # Whether the agents are actually live, rather than silently
                # running on their fallbacks. Never includes the key itself.
                "agents": _agent_status(),
                # Which provider is answering, how old the last confirmed read
                # of it is, and whether answers are currently degraded. "No
                # provider configured" and "running provider X" must be
                # different answers here, not one absent field.
                "llm": _llm_status(),
            },
        }

    return app


def _qdrant_up() -> bool:
    try:
        from app.vector.client import is_available

        return is_available()
    except Exception:
        return False


def _warn_about_unreadable_credentials() -> None:
    """Name the rows whose encryption key is not configured on this host.

    The alternative is discovering it when a Super Admin activates one, which
    is both later and harder to read: the row would fail authentication with no
    way to tell "the secret is wrong" from "the secret is elsewhere".
    """
    try:
        from sqlalchemy import text

        from app.core.database import SessionLocal
        from config.settings import settings

        known = set(settings.encryption_keys())
        session = SessionLocal()
        try:
            rows = session.execute(
                text("SELECT DISTINCT encryption_key_id FROM llm_providers")
            ).scalars().all()
        finally:
            session.close()

        missing = sorted(set(rows) - known)
        if missing:
            logger.warning(
                "Stored credentials reference encryption keys that are not "
                "configured — those providers cannot be used",
                extra={"missing_key_ids": missing, "configured_key_ids": sorted(known)},
            )
    except Exception as exc:
        # Table absent (not migrated yet) or database unreachable. Both are
        # already reported by /health; this check is not the place to complain.
        logger.info("Credential key check skipped",
                    extra={"error_type": type(exc).__name__})


def _llm_status() -> dict[str, object]:
    """The active provider, resolved.

    ``llm_config.describe()`` reads the cache and nothing else, so on a process
    that has not answered a request yet it would report "no provider" for a
    configuration that exists. Resolving first is what makes the two cases
    distinguishable, which is the whole reason this block is here.

    The resolve is deliberate and cheap: one indexed row, and `is_configured`
    never raises. It is also what stops this field from depending on the order
    keys happen to be evaluated in above.
    """
    try:
        from app.core import crypto
        from app.modules.ai import llm_client

        llm_client.is_configured()
        return {**llm_client.describe(),
                "encryption_configured": crypto.is_configured()}
    except Exception as exc:
        return {"configured": False, "error_type": type(exc).__name__}


def _agent_status() -> dict[str, object]:
    try:
        from app.modules.ai import crew

        return crew.describe()
    except Exception as exc:
        return {"agents_enabled": False, "error_type": type(exc).__name__}


app = create_app()
