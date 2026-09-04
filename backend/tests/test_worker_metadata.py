"""Every entry point must map every model.

SQLAlchemy resolves a string foreign key target lazily, against whatever is in
``Base.metadata`` when the first flush happens. Release 2 pointed
``documents.created_by`` at ``organization_admins`` and ``documents.
organization_id`` at ``organizations``, both defined in a different package - so
a process that imports the documents models alone raises
``NoReferencedTableError`` on its first write.

That is not hypothetical. The Celery worker did exactly this, and every document
uploaded after the Release 2 migration failed at its first state transition:

    NoReferencedTableError: Foreign key associated with column
    'documents.created_by' could not find table 'organization_admins'

Nothing in the API suite could catch it. Importing an app router pulls in every
model on the way to registering routes, so by the time any ordinary test runs,
the metadata is complete no matter what the code under test imported. These
tests therefore run in a **fresh interpreter** - the only place the omission is
still visible.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Tables reached by a foreign key from another package. If a model file grows a
# cross-package FK, add its target here.
CROSS_PACKAGE_TABLES = ("organizations", "organization_admins", "document_categories")


def _metadata_tables(entry_point: str) -> set[str]:
    """Import one entry point in a clean interpreter, return its mapped tables."""
    script = (
        f"import {entry_point}\n"
        "from app.core.database import Base\n"
        "print(' '.join(sorted(Base.metadata.tables)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split())


@pytest.mark.parametrize(
    "entry_point",
    [
        # The worker. This is the one that was broken.
        "app.workers.celery_app",
        "app.workers.tasks.process_document",
        # The API, which only ever worked by accident of import order.
        "app.main",
        # Alembic's registry, which is where the habit came from.
        "app.models_registry",
    ],
)
def test_entry_point_maps_every_cross_package_table(entry_point):
    tables = _metadata_tables(entry_point)
    missing = [t for t in CROSS_PACKAGE_TABLES if t not in tables]
    assert not missing, (
        f"{entry_point} maps 'documents' but not {missing}. Its first flush will "
        f"raise NoReferencedTableError. Import app.models_registry from it."
    )


def test_the_worker_can_actually_resolve_the_document_mapper():
    """The property the table list is a proxy for.

    ``_sorted_tables`` is what ``Session.flush`` walks, and it is where the
    failure surfaced. Resolving it without an exception is the real assertion;
    it needs no database.
    """
    script = (
        "import app.workers.tasks.process_document\n"
        "from sqlalchemy import inspect\n"
        "from app.modules.documents.models import Document\n"
        "inspect(Document).base_mapper._sorted_tables\n"
        "print('resolved')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert "resolved" in result.stdout
