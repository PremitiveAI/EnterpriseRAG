"""Application settings.

Single source of configuration. No module may call ``os.getenv`` directly, and
no business limit may be written in more than one place (spec §50).
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Credential-encryption secrets must be at least this long, for the same reason
# JWT_SECRET must be: the key is derived from the string, so a short one is a
# short key however it is stretched.
MIN_ENCRYPTION_SECRET_CHARS = 32


def parse_encryption_keys(raw: str | None) -> dict[int, str]:
    """Parse ``ENCRYPTION_KEYS`` — ``"1:secret-one,2:secret-two"``.

    Several keys, not one, so a rotation can be in progress without any row
    becoming unreadable (ADR-010 §6). Returns ``{}`` when unset: a host that has
    not been configured yet is not a misconfigured host, and the difference
    matters because only the second should stop the process.

    A secret may contain ``:`` — only the first one separates the id — but may
    not contain ``,``.
    """
    if raw is None or not raw.strip():
        return {}

    keys: dict[int, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue

        if ":" not in entry:
            raise ValueError(
                "ENCRYPTION_KEYS entries must be '<id>:<secret>'. "
                "Got an entry with no ':' separator."
            )

        raw_id, secret = entry.split(":", 1)
        raw_id, secret = raw_id.strip(), secret.strip()

        try:
            key_id = int(raw_id)
        except ValueError:
            raise ValueError(
                f"ENCRYPTION_KEYS id {raw_id!r} is not an integer."
            ) from None

        if key_id < 1:
            raise ValueError(f"ENCRYPTION_KEYS ids must be >= 1. Got {key_id}.")
        if key_id in keys:
            # Silently keeping the last one would mean rows encrypted under a
            # key the registry claims is something else.
            raise ValueError(f"ENCRYPTION_KEYS contains id {key_id} more than once.")
        if len(secret) < MIN_ENCRYPTION_SECRET_CHARS:
            raise ValueError(
                f"ENCRYPTION_KEYS secret for id {key_id} must be at least "
                f"{MIN_ENCRYPTION_SECRET_CHARS} characters."
            )

        keys[key_id] = secret

    return keys


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    APP_NAME: str = "EnterpriseRAG"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # --- Database ---
    DATABASE_URL: str
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Qdrant ---
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "documents"

    # --- Auth ---
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 30
    REFRESH_TOKEN_DAYS: int = 7
    BCRYPT_ROUNDS: int = 12

    # --- Credential encryption (ADR-010) ---
    # "<id>:<secret>,<id>:<secret>". More than one so a rotation can be in
    # progress; every ciphertext names the key it was written under.
    ENCRYPTION_KEYS: str | None = None
    # New credentials are written under this id. Old ones keep decrypting under
    # theirs, which is what makes a rotation a background job rather than an
    # outage.
    ENCRYPTION_ACTIVE_KEY_ID: int = 1

    # --- CORS ---
    # NoDecode: pydantic-settings would otherwise try to JSON-parse this value
    # straight from .env, before any validator runs, and a plain
    # "http://localhost:3000" is not JSON. NoDecode hands the raw string to
    # _split_csv below instead.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # --- Embeddings (ADR-003) ---
    EMBEDDING_MODEL: str = "sentence-transformers/all-mpnet-base-v2"
    EMBEDDING_DIM: int = 768

    # --- OCR (ADR-004) ---
    TESSERACT_CMD: str | None = None
    OCR_LANGUAGE: str = "eng"
    OCR_MIN_CHARS_PER_PAGE: int = 50

    # --- LLM provider (ADR-010) ---
    # GEMINI_API_KEY and GEMINI_MODEL used to live here. They are gone: the
    # active provider is a row in `llm_providers`, and there is deliberately no
    # environment fallback. A fallback would let a host answer from a stale key
    # that nothing in the UI mentions - the failure the seed migration exists to
    # prevent. If no provider is active the API says so, at /health and as
    # LLM_NO_ACTIVE_PROVIDER.
    #
    # The seed migration still reads both from the environment directly, because
    # a migration must work against the environment as it was.

    # --- LLM provider resolution (ADR-010 §5) ---
    # How long a cached configuration may keep answering after the database
    # stopped being readable. Below this the system prefers availability; above
    # it, safety - a revoked credential must stop working in bounded time.
    LLM_CONFIG_STALENESS_SECONDS: int = 300

    # --- Storage (§45) ---
    STORAGE_BACKEND: Literal["local"] = "local"
    STORAGE_ROOT: Path = BACKEND_ROOT / "storage"

    # --- Upload limits (§15) ---
    MAX_DOCUMENT_SIZE_MB: int = 20
    MAX_IMAGE_SIZE_MB: int = 2
    MAX_FILES_PER_BATCH: int = 20
    ALLOWED_DOCUMENT_EXTENSIONS: Annotated[set[str], NoDecode] = {"pdf", "docx", "pptx", "txt"}
    ALLOWED_IMAGE_EXTENSIONS: Annotated[set[str], NoDecode] = {"jpg", "jpeg", "png", "webp"}
    # Rejected with a specific code rather than a generic one (§15 deviation).
    LEGACY_EXTENSIONS: Annotated[set[str], NoDecode] = {"doc", "ppt"}

    # --- Chunking (§26) ---
    CHUNK_SIZE_CHARS: int = 1000
    CHUNK_OVERLAP_CHARS: int = 150

    # --- Retrieval (§32) ---
    RETRIEVAL_TOP_K: int = 5
    RETRIEVAL_MIN_SCORE: float = 0.35

    # --- Duplicate detection (ADR-005) ---
    DELETE_DUPLICATE_SOURCE_FILE: bool = True
    SEMANTIC_DUPLICATE_ENABLED: bool = False
    SEMANTIC_DUPLICATE_THRESHOLD: float = 0.95

    # --- Document management (§28-§30) ---
    DEFAULT_PAGE_SIZE: int = 25
    MAX_PAGE_SIZE: int = 100
    # Soft delete keeps the row so citations resolve; keeping the blob too means
    # a deletion is recoverable. Turn on only when storage cost outweighs that.
    DELETE_SOURCE_FILE_ON_DELETE: bool = False

    # --- Agent guards (docs/ai/agents.md) ---
    # Measured, not guessed. A trivial CrewAI round-trip to gemini-flash takes
    # ~15s on this setup — the framework adds real overhead on top of the model.
    # The original 8s for the planner meant it timed out on EVERY request, then
    # retried, burning ~16s to arrive at the fallback it would have used anyway.
    AGENT_PLAN_TIMEOUT_SECONDS: int = 25
    AGENT_COMPOSE_TIMEOUT_SECONDS: int = 60
    # Retries are off for the planner: its fallback (search the raw question) is
    # equivalent for a well-formed question, so a retry buys little and costs a
    # full timeout on the one path the user is waiting on.
    AGENT_PLAN_RETRIES: int = 0
    AGENT_COMPOSE_RETRIES: int = 1

    # --- Dynamic agent rules (ADR-009) ---
    # Where a Super Admin's edited prompts live. Under config/, which is the
    # application-controlled configuration package, and deliberately not under
    # storage/ (tenant document data) or logs/ (write-only, rotated).
    #
    # The directory is settable so the test suite can point at a temporary one.
    # A suite that wrote to the real directory and failed before restoring it
    # would leave every later test - and the next developer's chat - running an
    # unrelated persona.
    AGENT_RULES_DIR: Path = BACKEND_ROOT / "config" / "agent_rules"

    # --- Public chatbot: the same agents, on a much shorter leash ---
    #
    # A website visitor is not a signed-in admin who knows a query is running.
    # They are looking at a 400px panel with a spinner, and they leave. The
    # authenticated budget allows 25s planning + 2x60s composing = 145s, which
    # is longer than the browser will wait, so the visitor sees a timeout
    # instead of an answer that was on its way.
    #
    # A short answer from the top passages beats a better answer nobody stays
    # for, so the public path composes once, briefly, within 20 seconds.
    PUBLIC_AGENT_COMPOSE_TIMEOUT_SECONDS: int = 20
    PUBLIC_AGENT_COMPOSE_RETRIES: int = 0
    # Top-K for the public corpus. Fewer passages is materially faster: the
    # prompt is smaller and so is the answer the model has to write.
    PUBLIC_RETRIEVAL_TOP_K: int = 3

    # --- Chat context (§36) ---
    CHAT_CONTEXT_TURNS: int = 6
    CHAT_CONTEXT_TTL_SECONDS: int = 86_400
    MAX_MESSAGE_CHARS: int = 4000

    # --- Retention ---
    RETENTION_DAYS: int = 90

    # --- Rate limits (§40) ---
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_UPLOAD: str = "20/minute"
    RATE_LIMIT_CHAT: str = "30/minute"
    RATE_LIMIT_DEFAULT: str = "300/minute"

    # --- Logging (§39) ---
    LOG_LEVEL: str = "INFO"
    LOG_DIR: Path = BACKEND_ROOT / "logs"
    LOG_JSON: bool = True

    # ------------------------------------------------------------------ #

    # The GEMINI_MODEL validator that used to sit here stripped a provider
    # prefix so `gemini/gemini-2.0-flash` in .env did not become
    # `gemini/gemini/gemini-2.0-flash` at call time - a defect that made BOTH AI
    # paths fail silently into the templated fallback. That normalisation did
    # not disappear with the setting: it moved to the write path
    # (`LLMProviderCreate`) and is applied again when the id is composed
    # (`llm_client.model_id`), because a value can also reach the database from
    # a migration or hand-written SQL.

    @field_validator("JWT_SECRET")
    @classmethod
    def _secret_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept ``a,b`` from .env, or a JSON array, or an already-built list."""
        if isinstance(v, str):
            raw = v.strip()
            if raw.startswith("["):
                import json

                return json.loads(raw)
            return [o.strip() for o in raw.split(",") if o.strip()]
        return v

    @field_validator(
        "ALLOWED_DOCUMENT_EXTENSIONS",
        "ALLOWED_IMAGE_EXTENSIONS",
        "LEGACY_EXTENSIONS",
        mode="before",
    )
    @classmethod
    def _split_extensions(cls, v: object) -> object:
        if isinstance(v, str):
            return {e.strip().lower().lstrip(".") for e in v.split(",") if e.strip()}
        return v

    @property
    def max_document_bytes(self) -> int:
        return self.MAX_DOCUMENT_SIZE_MB * 1024 * 1024

    @property
    def max_image_bytes(self) -> int:
        return self.MAX_IMAGE_SIZE_MB * 1024 * 1024

    @property
    def allowed_extensions(self) -> set[str]:
        return self.ALLOWED_DOCUMENT_EXTENSIONS | self.ALLOWED_IMAGE_EXTENSIONS

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    def encryption_keys(self) -> dict[int, str]:
        """The parsed key registry, or ``{}`` when encryption is unconfigured.

        Parsed on each call rather than cached: the value is tiny, it is read
        once per credential decryption, and a cached copy is one more place a
        secret would live.
        """
        return parse_encryption_keys(self.ENCRYPTION_KEYS)

    @model_validator(mode="after")
    def _encryption_registry_is_usable(self) -> "Settings":
        """Reject a broken key registry at import, not at first use.

        This is the one encryption check that hard-fails. It is pure
        configuration — no database, no network — so it cannot be a startup
        warning in the way ``main.py``'s dependency checks are. A registry whose
        active id names nothing would otherwise surface as a failure to save a
        credential, long after the mistake was made.
        """
        keys = parse_encryption_keys(self.ENCRYPTION_KEYS)
        if keys and self.ENCRYPTION_ACTIVE_KEY_ID not in keys:
            raise ValueError(
                f"ENCRYPTION_ACTIVE_KEY_ID={self.ENCRYPTION_ACTIVE_KEY_ID} is not "
                f"one of the ids in ENCRYPTION_KEYS ({sorted(keys)})."
            )
        return self

    def size_limit_for(self, extension: str) -> int:
        """Byte limit for a normalised extension."""
        ext = extension.lower().lstrip(".")
        if ext in self.ALLOWED_IMAGE_EXTENSIONS:
            return self.max_image_bytes
        return self.max_document_bytes


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
