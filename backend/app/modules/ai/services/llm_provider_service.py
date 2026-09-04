"""Super Admin LLM provider management (ADR-010 §7).

The ordering in :meth:`activate` is the design, and it is worth stating plainly
because both halves look optional and neither is:

**The connection test runs outside any transaction.** It is a third-party HTTP
call. Holding a transaction across one would hold row locks for its duration,
leave a pooled connection ``idle in transaction`` for a round trip that is
routinely fifteen seconds, and — against a blackholed socket — until the SDK's
own timeout. ``idle_in_transaction_session_timeout`` would eventually kill it
mid-call and surface as a confusing commit error rather than as the provider
timeout it actually is.

**The fingerprint is re-checked inside the transaction.** Testing outside opens
a window in which another Super Admin edits the same row, so what was verified
would not be what goes live. Comparing the fingerprint captured at test time
against the row re-read under lock closes it, and answers 409.

Like `agent_rule.updated`, the audit row is the compensating control: a Super
Admin can repoint every tenant's answering model, authorization cannot restrict
that, so the guarantee is that it **cannot be exercised silently**.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.exceptions import (
    LLMProviderActiveError,
    LLMProviderChangedError,
    LLMProviderNotFoundError,
    LLMProviderTestFailedError,
)
from app.core.logging import get_logger
from app.modules.ai import llm_client, llm_config
from app.modules.ai.models import LLMProviderName
from app.modules.ai.schemas.llm_provider import (
    LLMProviderCreate,
    LLMProviderListResponse,
    LLMProviderOut,
    LLMProviderTestResult,
    LLMProviderUpdate,
)
from app.modules.auth.models import ActorType
from app.modules.auth.repositories.user_repository import AuditRepository

logger = get_logger(__name__)

PROVIDER_CREATED = "llm_provider.created"
PROVIDER_UPDATED = "llm_provider.updated"
PROVIDER_ACTIVATED = "llm_provider.activated"
PROVIDER_DELETED = "llm_provider.deleted"

# Short. This is a liveness check, not a workload, and a Super Admin is waiting
# on the response.
TEST_TIMEOUT_SECONDS = 20
TEST_PROMPT = "Reply with the single word: OK"

_COLUMNS = """
    id, provider_name, model_name, encrypted_api_key, encryption_key_id,
    key_fingerprint, base_url, config, is_active, config_version,
    last_tested_at, created_at, updated_at
"""


class LLMProviderService:
    def __init__(self, db: Session, super_admin_id: UUID) -> None:
        self.db = db
        self.super_admin_id = super_admin_id
        self.audit = AuditRepository(db)

    # --- Reads -------------------------------------------------------- #

    def list(self) -> LLMProviderListResponse:
        rows = self.db.execute(
            text(f"SELECT {_COLUMNS} FROM llm_providers "
                 "ORDER BY is_active DESC, created_at DESC")
        ).all()
        return LLMProviderListResponse(items=[self._out(row) for row in rows])

    def get(self, provider_id: UUID) -> LLMProviderOut:
        return self._out(self._require(provider_id))

    # --- Writes ------------------------------------------------------- #

    def create(self, payload: LLMProviderCreate) -> LLMProviderOut:
        """Register a provider, inactive, after proving the credential works.

        Registering and activating are separate on purpose: a Super Admin must
        be able to prepare a provider without pointing live traffic at it.
        """
        stored = crypto.encrypt(payload.api_key)
        self._test_credential(
            provider=payload.provider_name, model=payload.model_name,
            api_key=payload.api_key, base_url=payload.base_url,
        )

        row = self.db.execute(
            text(
                f"""
                INSERT INTO llm_providers
                    (provider_name, model_name, encrypted_api_key,
                     encryption_key_id, key_fingerprint, base_url, config,
                     is_active, last_tested_at, created_by)
                VALUES (:provider, :model, :token, :key_id, :fingerprint,
                        :base_url, CAST(:config AS jsonb), false, now(), :actor)
                RETURNING {_COLUMNS}
                """
            ),
            {
                "provider": payload.provider_name.value,
                "model": payload.model_name,
                "token": stored.token,
                "key_id": stored.key_id,
                "fingerprint": stored.fingerprint,
                "base_url": payload.base_url,
                "config": _json(payload.config),
                "actor": self.super_admin_id,
            },
        ).one()

        self._record(PROVIDER_CREATED, row)
        self.db.commit()
        return self._out(row)

    def update(self, provider_id: UUID, payload: LLMProviderUpdate) -> LLMProviderOut:
        """Change a provider. A replacement credential is proven before storing.

        Omitting ``api_key`` keeps the stored one — a blank field in a form must
        not be how a working credential gets erased.
        """
        current = self._require(provider_id)

        model = payload.model_name if payload.model_name is not None else current.model_name
        base_url = payload.base_url if payload.base_url is not None else current.base_url
        config = payload.config if payload.config is not None else (current.config or {})

        if payload.api_key is not None:
            stored = crypto.encrypt(payload.api_key)
            self._test_credential(
                provider=current.provider_name, model=model,
                api_key=payload.api_key, base_url=base_url,
            )
            token, key_id, fingerprint = stored.token, stored.key_id, stored.fingerprint
        else:
            token = current.encrypted_api_key
            key_id = current.encryption_key_id
            fingerprint = current.key_fingerprint

        row = self.db.execute(
            text(
                f"""
                UPDATE llm_providers
                   SET model_name = :model,
                       encrypted_api_key = :token,
                       encryption_key_id = :key_id,
                       key_fingerprint = :fingerprint,
                       base_url = :base_url,
                       config = CAST(:config AS jsonb)
                 WHERE id = :id
                RETURNING {_COLUMNS}
                """
            ),
            {
                "model": model, "token": token, "key_id": key_id,
                "fingerprint": fingerprint, "base_url": base_url,
                "config": _json(config), "id": provider_id,
            },
        ).one()

        self._record(PROVIDER_UPDATED, row,
                     extra={"credential_replaced": payload.api_key is not None})
        self.db.commit()
        return self._out(row)

    def test(self, provider_id: UUID) -> LLMProviderTestResult:
        """One real call against a stored provider, changing nothing else.

        Available on any row, active or not, so a credential can be checked
        before it is trusted with traffic.
        """
        row = self._require(provider_id)
        config = llm_config.build_config(row)

        self._call(config)

        self.db.execute(
            text("UPDATE llm_providers SET last_tested_at = now() WHERE id = :id"),
            {"id": provider_id},
        )
        self.db.commit()

        return LLMProviderTestResult(
            ok=True,
            provider_name=row.provider_name,
            model_name=row.model_name,
            resolved_model_id=llm_client.model_id(row.provider_name, row.model_name),
            tested_at=datetime.now(timezone.utc),
        )

    def activate(self, provider_id: UUID) -> LLMProviderOut:
        """Make this provider the one every agent answers from."""
        # 1. Read, and let go of the transaction before making an HTTP call.
        row = self._require(provider_id)
        verified_fingerprint = row.key_fingerprint
        config = llm_config.build_config(row)
        self.db.commit()

        # 2. Prove it works. Nothing has been written and nothing is locked.
        self._call(config)

        # 3. Commit the switch. The row is re-read under lock: what was tested
        #    must be what goes live.
        target = self.db.execute(
            text(f"SELECT {_COLUMNS} FROM llm_providers WHERE id = :id FOR UPDATE"),
            {"id": provider_id},
        ).one_or_none()

        if target is None:
            self.db.rollback()
            raise LLMProviderNotFoundError()

        if target.key_fingerprint != verified_fingerprint:
            self.db.rollback()
            logger.warning(
                "Provider activation aborted: the credential changed during verification",
                extra={"provider_id": str(provider_id)},
            )
            raise LLMProviderChangedError()

        # Deactivate BEFORE activating. The unique index is checked per
        # statement, so the other order collides with the row being stood down.
        self.db.execute(
            text("UPDATE llm_providers SET is_active = false "
                 "WHERE is_active AND id <> :id"),
            {"id": provider_id},
        )
        try:
            row = self.db.execute(
                text(f"UPDATE llm_providers SET is_active = true, last_tested_at = now() "
                     f"WHERE id = :id RETURNING {_COLUMNS}"),
                {"id": provider_id},
            ).one()
        except IntegrityError:
            # Two Super Admins activating at once. The database refused; say so
            # rather than leaving a half-applied switch.
            self.db.rollback()
            raise LLMProviderChangedError() from None

        self._record(PROVIDER_ACTIVATED, row)
        self.db.commit()

        logger.info(
            "LLM provider activated",
            extra={"provider": row.provider_name, "model": row.model_name,
                   "config_version": row.config_version},
        )
        return self._out(row)

    def delete(self, provider_id: UUID) -> None:
        row = self._require(provider_id)
        if row.is_active:
            raise LLMProviderActiveError()

        self.db.execute(text("DELETE FROM llm_providers WHERE id = :id"),
                        {"id": provider_id})
        self._record(PROVIDER_DELETED, row)
        self.db.commit()

    # --- internals ---------------------------------------------------- #

    def _require(self, provider_id: UUID):
        row = self.db.execute(
            text(f"SELECT {_COLUMNS} FROM llm_providers WHERE id = :id"),
            {"id": provider_id},
        ).one_or_none()
        if row is None:
            raise LLMProviderNotFoundError()
        return row

    def _test_credential(self, *, provider: LLMProviderName | str, model: str,
                         api_key: str, base_url: str | None) -> None:
        """Prove a credential that is not stored yet."""
        self._call(
            llm_config.LLMConfig(
                version=0,
                provider=str(provider.value if hasattr(provider, "value") else provider),
                model=model,
                api_key=api_key,
                base_url=base_url,
                fingerprint=crypto.fingerprint(api_key),
            )
        )

    def _call(self, config) -> None:
        """One real completion. Raises :class:`LLMProviderTestFailedError`.

        The reason is logged and never returned. A provider's error text can
        echo the request — and the request carries the credential.
        """
        try:
            reply = llm_client.build_llm(
                config, 0.0, TEST_TIMEOUT_SECONDS
            ).call(TEST_PROMPT)
        except Exception as exc:
            logger.warning(
                "Provider connection test failed",
                extra={"provider": config.provider, "model": config.model,
                       "error_type": type(exc).__name__},
            )
            raise LLMProviderTestFailedError() from exc

        if not (reply or "").strip():
            logger.warning("Provider connection test returned nothing",
                           extra={"provider": config.provider, "model": config.model})
            raise LLMProviderTestFailedError()

    def _record(self, action: str, row, *, extra: dict | None = None) -> None:
        """Audit. The fingerprint identifies the credential; the key never
        appears, in this row or in any log line."""
        self.audit.record(
            action=action,
            entity_type="llm_provider",
            entity_id=row.id,
            user_id=self.super_admin_id,
            actor_type=ActorType.SUPER_ADMIN,
            metadata={
                "provider": str(row.provider_name),
                "model": row.model_name,
                "key_fingerprint": row.key_fingerprint,
                "encryption_key_id": row.encryption_key_id,
                "config_version": row.config_version,
                **(extra or {}),
            },
        )

    @staticmethod
    def _out(row) -> LLMProviderOut:
        return LLMProviderOut(
            id=row.id,
            provider_name=row.provider_name,
            model_name=row.model_name,
            key_fingerprint=row.key_fingerprint,
            encryption_key_id=row.encryption_key_id,
            base_url=row.base_url,
            config=row.config or {},
            is_active=row.is_active,
            config_version=row.config_version,
            last_tested_at=row.last_tested_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _json(value: dict) -> str:
    import json

    return json.dumps(value or {})
