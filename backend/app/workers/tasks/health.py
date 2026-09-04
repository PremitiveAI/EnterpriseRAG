"""A trivial task, so the worker path can be verified before Phase 4."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="system.health")
def health(echo: str = "ok") -> dict[str, str]:
    logger.info("Health task executed", extra={"echo": echo})
    return {"status": "ok", "echo": echo, "at": datetime.now(timezone.utc).isoformat()}
