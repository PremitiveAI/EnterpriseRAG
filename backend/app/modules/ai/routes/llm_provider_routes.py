"""Super Admin LLM provider routes (ADR-010).

Mounted under ``/super-admin`` alongside organizations, categories and agent
rules. Providers are global: an Organization Admin cannot see or change them,
because a tenant switching the provider would be switching it for every other
tenant — and would be handling a credential that is not theirs.

Registering and activating are **two** routes. One that did both would make
"try a provider" and "point all traffic at it" the same gesture.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.principal import Principal, require_super_admin
from app.modules.ai.controllers.llm_provider_controller import LLMProviderController
from app.modules.ai.schemas.llm_provider import LLMProviderCreate, LLMProviderUpdate

router = APIRouter(prefix="/super-admin", tags=["super-admin"])


def controller(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_super_admin),
) -> LLMProviderController:
    return LLMProviderController(db, principal.subject_id)


@router.get("/llm-providers", response_model=None)
def list_providers(ctrl: LLMProviderController = Depends(controller)) -> dict:
    """Every registered provider. Never includes a credential."""
    return ctrl.list()


@router.get("/llm-providers/{provider_id}", response_model=None)
def get_provider(
    provider_id: UUID, ctrl: LLMProviderController = Depends(controller)
) -> dict:
    return ctrl.get(provider_id)


@router.post("/llm-providers", response_model=None)
def create_provider(
    payload: LLMProviderCreate, ctrl: LLMProviderController = Depends(controller)
) -> dict:
    """Register a provider. The credential is proven before it is stored, and
    the row is created inactive."""
    return ctrl.create(payload)


@router.patch("/llm-providers/{provider_id}", response_model=None)
def update_provider(
    provider_id: UUID,
    payload: LLMProviderUpdate,
    ctrl: LLMProviderController = Depends(controller),
) -> dict:
    """Change model, endpoint or config. Omitting ``api_key`` keeps the stored
    credential rather than clearing it."""
    return ctrl.update(provider_id, payload)


@router.post("/llm-providers/{provider_id}/test", response_model=None)
def test_provider(
    provider_id: UUID, ctrl: LLMProviderController = Depends(controller)
) -> dict:
    """One real call. Changes nothing except when it was last tested."""
    return ctrl.test(provider_id)


@router.post("/llm-providers/{provider_id}/activate", response_model=None)
def activate_provider(
    provider_id: UUID, ctrl: LLMProviderController = Depends(controller)
) -> dict:
    """Switch every agent to this provider, in every process, with no restart."""
    return ctrl.activate(provider_id)


@router.delete("/llm-providers/{provider_id}", response_model=None)
def delete_provider(
    provider_id: UUID, ctrl: LLMProviderController = Depends(controller)
) -> dict:
    """Only an inactive provider. Deleting the live one is a 409."""
    return ctrl.delete(provider_id)
