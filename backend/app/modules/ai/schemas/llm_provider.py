"""LLM provider contracts (docs/release-2/features/llm-provider-management.md §7).

The credential travels **in only**. No response model here has a field for it,
which is a stronger guarantee than remembering to exclude one: a key cannot be
returned by a route that has nowhere to put it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.modules.ai.models import LLMProviderName


class LLMProviderOut(BaseModel):
    """What a Super Admin sees. The fingerprint stands in for the key.

    It is enough to answer "is this the same credential as before?" and
    "did my paste take effect?" without any route ever decrypting one.
    """

    id: UUID
    provider_name: LLMProviderName
    model_name: str
    key_fingerprint: str
    encryption_key_id: int
    base_url: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    config_version: int
    last_tested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class LLMProviderListResponse(BaseModel):
    items: list[LLMProviderOut]


class _ModelNameMixin(BaseModel):
    @field_validator("model_name", mode="before", check_fields=False)
    @classmethod
    def _store_the_bare_id(cls, value: object) -> object:
        """Strip any provider prefix before it reaches the database.

        ``openai/gpt-4o`` and ``gpt-4o`` must not become two different rows, and
        the composed id is built in exactly one place at call time. A stored
        prefix would be doubled there - the defect that once made every answer
        come silently from the templated fallback.
        """
        if isinstance(value, str):
            from app.modules.ai.llm_client import bare_model

            return bare_model(value)
        return value


class LLMProviderCreate(_ModelNameMixin):
    provider_name: LLMProviderName
    model_name: str = Field(min_length=1, max_length=120)
    api_key: str = Field(min_length=8, max_length=500)
    base_url: str | None = Field(default=None, max_length=500)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("api_key")
    @classmethod
    def _no_surrounding_whitespace(cls, value: str) -> str:
        """A pasted key routinely arrives with a trailing newline, and the
        provider rejects it with an authentication error that points nowhere
        near the paste."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("The API key is empty.")
        return stripped


class LLMProviderUpdate(_ModelNameMixin):
    """Every field optional. Omitting ``api_key`` keeps the stored credential —
    it is not cleared, because a blank field in a form is how a key gets
    accidentally erased."""

    model_name: str | None = Field(default=None, min_length=1, max_length=120)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)
    base_url: str | None = Field(default=None, max_length=500)
    config: dict[str, Any] | None = None

    @field_validator("api_key")
    @classmethod
    def _no_surrounding_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("The API key is empty.")
        return stripped


class LLMProviderTestResult(BaseModel):
    """The outcome of one real call, for the Test button."""

    ok: bool
    provider_name: LLMProviderName
    model_name: str
    resolved_model_id: str
    tested_at: datetime
