"""LLM provider configuration (ADR-010).

Schema of record: docs/release-2/features/llm-provider-management.md §6.

The table holds every registered provider; exactly one row is active, and that
row is what every agent answers from. Three of the constraints here are the
design rather than decoration, and each is enforced by the database because the
application-level version of it fails silently:

* **One active row**, by partial unique index. Two concurrent activations cannot
  both win.
* **``config_version`` from a global sequence**, by trigger. A per-row counter
  lets two rows hold the same version, and a process warm on the wrong one then
  compares ``2 == 2`` and never rebuilds.
* **The credential is a ciphertext plus the id of the key that wrote it.** A
  rotation is otherwise unrecoverable — see ``app/core/crypto.py``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

VERSION_SEQUENCE = "llm_config_version_seq"


class LLMProviderName(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE = "azure"


class LLMProvider(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One registered provider. At most one is active.

    Activation order matters and is not arbitrary: the current row must be
    deactivated **before** the new one is activated. The unique index is checked
    per statement, so activating first would collide with the row that is about
    to be stood down.
    """

    __tablename__ = "llm_providers"

    provider_name: Mapped[LLMProviderName] = mapped_column(
        SAEnum(LLMProviderName, name="llm_provider_name", native_enum=True),
        nullable=False,
    )

    # The BARE model id — "gpt-4o", never "openai/gpt-4o". LiteLLM wants the
    # prefix and the raw SDKs reject it, so it is composed in exactly one place
    # (the client factory) and stored nowhere. Writing a prefixed id here once
    # produced "gemini/gemini/gemini-2.0-flash" and every answer silently came
    # from the templated fallback.
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)

    # "v1.<key id>.<base64url>" — AES-256-GCM. Never logged, never returned by
    # any route, never included in an error message.
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Duplicates the id inside the ciphertext on purpose: the token is
    # authoritative for decrypting, and this column is how a re-encryption job
    # finds the rows that still need work without decrypting all of them.
    encryption_key_id: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )

    # sha256 of the PLAINTEXT, truncated. Lets the UI, the audit log and the
    # activation conflict check compare credentials without decrypting one.
    key_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)

    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Temperature, max tokens, timeouts. Per-agent settings win over anything
    # here: agents 1 and 2 need determinism, and a provider-level default must
    # not be able to loosen them.
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # Monotonic across ALL rows, not per row. Set by the database on insert and
    # on any update that changes the resolved configuration; application code
    # never assigns it.
    # ``::regclass`` is how PostgreSQL stores the default back, and Alembic
    # compares these as strings - without the cast, ``alembic check`` reports a
    # permanent phantom diff and the next autogenerate emits a pointless
    # modify_default.
    config_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text(f"nextval('{VERSION_SEQUENCE}'::regclass)"),
    )

    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The Super Admin who registered it. Nullable because the seeded row was
    # created by a migration, not by a person.
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_llm_providers_one_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        """Deliberately excludes the credential, including its ciphertext."""
        return (
            f"<LLMProvider {self.provider_name}/{self.model_name} "
            f"active={self.is_active} v={self.config_version}>"
        )
