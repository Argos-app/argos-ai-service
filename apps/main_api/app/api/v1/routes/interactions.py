"""Rotas para enviar mensagens ou respostas a perguntas."""

from uuid import UUID

from fastapi import APIRouter

from app.api.v1.dependencies.auth import PrincipalDep
from app.api.v1.dependencies.services import SessionServiceDep
from app.api.v1.models.interactions import InteractionInput, InteractionResult

router = APIRouter(prefix="/sessions/{session_id}/interactions", tags=["interactions"])


@router.post("", response_model=InteractionResult)
async def create_interaction(
    session_id: UUID,
    body: InteractionInput,
    principal: PrincipalDep,
    service: SessionServiceDep,
) -> InteractionResult:
    return await service.create_interaction(principal, session_id, body)
