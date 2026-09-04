"""Translates Super Admin provider requests into service calls (spec §12)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.ai.schemas.llm_provider import LLMProviderCreate, LLMProviderUpdate
from app.modules.ai.services.llm_provider_service import LLMProviderService


class LLMProviderController:
    def __init__(self, db: Session, super_admin_id: UUID) -> None:
        self.service = LLMProviderService(db, super_admin_id)

    def list(self) -> dict:
        return {"success": True, "data": self.service.list().model_dump(mode="json")}

    def get(self, provider_id: UUID) -> dict:
        return {"success": True,
                "data": self.service.get(provider_id).model_dump(mode="json")}

    def create(self, payload: LLMProviderCreate) -> dict:
        provider = self.service.create(payload)
        return {
            "success": True,
            # Says what did NOT happen. A Super Admin who reads "Saved" after a
            # successful test will reasonably assume traffic has moved.
            "message": (
                f"{provider.provider_name} registered and verified. "
                "It is not answering yet — activate it to switch."
            ),
            "data": provider.model_dump(mode="json"),
        }

    def update(self, provider_id: UUID, payload: LLMProviderUpdate) -> dict:
        return {"success": True,
                "data": self.service.update(provider_id, payload).model_dump(mode="json")}

    def test(self, provider_id: UUID) -> dict:
        result = self.service.test(provider_id)
        return {"success": True, "message": "The provider answered.",
                "data": result.model_dump(mode="json")}

    def activate(self, provider_id: UUID) -> dict:
        provider = self.service.activate(provider_id)
        return {
            "success": True,
            "message": (
                f"Now answering with {provider.provider_name}/{provider.model_name}. "
                "It applies to the next request — no restart."
            ),
            "data": provider.model_dump(mode="json"),
        }

    def delete(self, provider_id: UUID) -> dict:
        self.service.delete(provider_id)
        return {"success": True, "message": "Provider deleted.", "data": None}
