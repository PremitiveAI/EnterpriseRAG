"""Celery application (spec §23, docs/celery/tasks.md).

Run on Windows with:
    celery -A app.workers.celery_app worker -l info --pool=solo
"""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging

from app.core.logging import configure_logging, get_logger

# Every model, in every worker process. NOT optional and NOT only for Alembic.
#
# SQLAlchemy resolves a string foreign key target lazily, against whatever is in
# Base.metadata at the moment of the first flush. The worker used to import only
# the documents models, so `documents.created_by -> organization_admins.id` had
# no table to resolve to and EVERY document failed at its first commit with
# NoReferencedTableError. The API never showed it, because its routers happen to
# import every model on the way to registering routes - which is luck, not
# design, and luck the worker did not share.
from app import models_registry  # noqa: F401
from config.settings import settings

logger = get_logger(__name__)

celery_app = Celery("enterprise_rag", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Ack after completion, not on receipt: a worker killed mid-document must
    # cause redelivery, not silent loss. Idempotency makes the rerun safe.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # These tasks run for minutes. The default prefetch would let one worker
    # reserve documents it cannot start for an hour.
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=86_400,
    task_default_queue="default",
    # Every task module MUST be listed here. A task that is enqueued but not
    # imported by the worker fails with NotRegistered, and the document sits at
    # QUEUED forever with no visible error on the API side.
    imports=(
        "app.workers.tasks.health",
        "app.workers.tasks.process_document",
    ),
)


@setup_logging.connect
def _configure_worker_logging(**_kwargs: object) -> None:
    """Use the application's JSON logging and PII filter inside the worker too."""
    configure_logging()


__all__ = ["celery_app"]
