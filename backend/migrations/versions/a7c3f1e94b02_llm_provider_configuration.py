"""llm provider configuration

Release 2.1, ADR-010. Two things happen here and they must not be separated:
the table is created, and the provider currently living in ``.env`` is seeded
into it.

Without the seed, an upgrade is a **silent outage**. The application starts, no
provider is active, and every chat falls back to the templated composer — HTTP
200, a plausible-looking answer, no error code and nothing in the log.
``docs/implementation-status.md`` already records that exact failure once.

Revision ID: a7c3f1e94b02
Revises: 56cebd87d211
Create Date: 2026-09-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a7c3f1e94b02'
down_revision: Union[str, None] = '56cebd87d211'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Schema
    # ------------------------------------------------------------------ #

    # Global, not per row. A per-row counter lets two rows independently reach
    # version 2; a process warm on the inactive one then compares 2 == 2 and
    # never rebuilds, answering from the wrong provider while the database, the
    # audit log and the UI all say otherwise. Nothing raises.
    op.execute("CREATE SEQUENCE llm_config_version_seq")

    op.create_table(
        "llm_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_name",
                  postgresql.ENUM("gemini", "openai", "anthropic", "azure",
                                  name="llm_provider_name", create_type=True),
                  nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.SmallInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("key_fingerprint", sa.String(16), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("config_version", sa.BigInteger(), nullable=False,
                  server_default=sa.text("nextval('llm_config_version_seq')")),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # One active provider, enforced here rather than in application code so two
    # concurrent activations cannot both win.
    op.execute(
        "CREATE UNIQUE INDEX ix_llm_providers_one_active "
        "ON llm_providers (is_active) WHERE is_active"
    )

    # The version bump is a trigger and not application code for the same
    # reason: a single forgotten assignment in one write path reproduces the
    # collision the sequence exists to prevent, and it fails silently.
    #
    # The WHEN clause keeps bookkeeping writes — last_tested_at in particular —
    # from bumping the version and making every process rebuild a client whose
    # configuration did not change.
    op.execute(
        """
        CREATE FUNCTION llm_providers_bump_version() RETURNS trigger AS $$
        BEGIN
            NEW.config_version := nextval('llm_config_version_seq');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_llm_providers_bump_version
        BEFORE UPDATE ON llm_providers
        FOR EACH ROW
        WHEN (
            OLD.provider_name     IS DISTINCT FROM NEW.provider_name
         OR OLD.model_name        IS DISTINCT FROM NEW.model_name
         OR OLD.encrypted_api_key IS DISTINCT FROM NEW.encrypted_api_key
         OR OLD.encryption_key_id IS DISTINCT FROM NEW.encryption_key_id
         OR OLD.base_url          IS DISTINCT FROM NEW.base_url
         OR OLD.config            IS DISTINCT FROM NEW.config
         OR OLD.is_active         IS DISTINCT FROM NEW.is_active
        )
        EXECUTE FUNCTION llm_providers_bump_version()
        """
    )

    # ------------------------------------------------------------------ #
    # 2. Seed from .env
    # ------------------------------------------------------------------ #
    _seed_from_environment()


def _seed_from_environment() -> None:
    """Move the provider in ``.env`` into the table.

    Three outcomes, and each is deliberate:

    * **No ``GEMINI_API_KEY``** — nothing to seed. The upgrade succeeds and the
      system has no active provider, which is the truthful state of a host that
      had no working AI before this migration either. ``/health`` says so.
    * **A key, but no ``ENCRYPTION_KEYS``** — hard failure. Seeding would mean
      either storing the credential in plaintext or dropping it silently, and
      both are worse than refusing to migrate.
    * **A key and a registry** — one active row, encrypted under the active key.

    Reads the environment directly, and this is the one place in the codebase
    that should. ``settings`` is the application's *current* configuration and
    is free to change; a data migration must keep working against the
    environment as it was when the migration was written. ``GEMINI_API_KEY``
    leaves the settings model in the very next phase, and this migration still
    has to run on a host that has not upgraded yet.
    """
    import os

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        print(
            "llm_providers: no GEMINI_API_KEY in the environment, so no provider "
            "was seeded. Register one at /super-admin/llm-providers."
        )
        return

    from app.core import crypto

    if not crypto.is_configured():
        raise RuntimeError(
            "GEMINI_API_KEY is set but ENCRYPTION_KEYS is not, so the credential "
            "cannot be encrypted. Add ENCRYPTION_KEYS to .env (see .env.example) "
            "and run this migration again."
        )

    # The BARE id. A stored prefix would be doubled when the client factory adds
    # the real one - the defect that once made every answer come silently from
    # the templated fallback. Spelled out here rather than imported, because a
    # migration must not depend on application code that can change under it.
    model = (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash").strip()
    for prefix in ("gemini/", "google/", "models/"):
        if model.lower().startswith(prefix):
            model = model[len(prefix):]

    stored = crypto.encrypt(api_key)

    # Idempotent by intent rather than by accident: Alembic will not re-run this
    # revision, but a hand-run of the function must not create a second active
    # row and trip the unique index.
    connection = op.get_bind()
    already = connection.execute(
        sa.text("SELECT count(*) FROM llm_providers")
    ).scalar_one()
    if already:
        print("llm_providers: rows already present, seed skipped.")
        return

    connection.execute(
        sa.text(
            """
            INSERT INTO llm_providers
                (provider_name, model_name, encrypted_api_key,
                 encryption_key_id, key_fingerprint, config, is_active)
            VALUES
                (:provider, :model, :token, :key_id, :fingerprint,
                 '{}'::jsonb, true)
            """
        ),
        {
            "provider": "gemini",
            "model": model,
            "token": stored.token,
            "key_id": stored.key_id,
            "fingerprint": stored.fingerprint,
        },
    )
    print(
        f"llm_providers: seeded gemini/{model} as the active "
        f"provider (key fingerprint {stored.fingerprint})."
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_llm_providers_bump_version ON llm_providers")
    op.execute("DROP FUNCTION IF EXISTS llm_providers_bump_version()")
    op.drop_table("llm_providers")
    op.execute("DROP TYPE IF EXISTS llm_provider_name")
    op.execute("DROP SEQUENCE IF EXISTS llm_config_version_seq")
