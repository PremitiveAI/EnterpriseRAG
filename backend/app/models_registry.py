"""Imports every model, so any process that maps them has the full metadata.

Import this module - not the individual model modules - from every entry point:
``migrations/env.py``, ``app/main.py`` and ``app/workers/celery_app.py``.

This is not only an Alembic convenience. Release 2 gave ``documents`` foreign
keys into ``organizations`` and ``organization_admins``, and SQLAlchemy resolves
a string FK target lazily against ``Base.metadata`` at the first flush. A process
that imports one model package and not the others therefore raises
``NoReferencedTableError`` on its first write - which is exactly what the Celery
worker did to every document until it started importing this module.
"""

from app.core.database import Base  # noqa: F401
from app.modules.ai.models import LLMProvider  # noqa: F401
from app.modules.auth.models import AuditLog, User  # noqa: F401
from app.modules.chat.models import (  # noqa: F401
    ChatMessage,
    Conversation,
    MessageSource,
)
from app.modules.organizations.models import (  # noqa: F401
    Organization,
    OrganizationAdmin,
    OtpRequest,
)
from app.modules.documents.models import (  # noqa: F401
    Document,
    DocumentCategory,
    DocumentChunk,
    DocumentProcessing,
    DocumentTag,
)

__all__ = ["Base"]
