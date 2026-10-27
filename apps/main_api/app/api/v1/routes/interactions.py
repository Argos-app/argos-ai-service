"""Rotas para enviar mensagens ou respostas a perguntas."""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.v1.dependencies.auth import PrincipalDep
from app.api.v1.dependencies.services import SessionServiceDep
from app.api.v1.mappers import (
    actor_from_http,
    interaction_command_from_http,
    interaction_result_to_http,
)
from app.api.v1.models.interactions import (
    InteractionAccepted,
    InteractionInput,
    InteractionOperationStatus,
)

router = APIRouter(prefix="/sessions/{session_id}/interactions", tags=["interactions"])


@router.post(
    "/async",
    response_model=InteractionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_interaction_async(
    session_id: UUID,
    body: InteractionInput,
    principal: PrincipalDep,
    service: SessionServiceDep,
) -> InteractionAccepted:
    """Aceita a interação e deixa o processamento seguir em segundo plano."""
    operation_id = await service.submit_interaction(
        actor_from_http(principal),
        session_id,
        interaction_command_from_http(body),
    )
    return InteractionAccepted(operation_id=operation_id)


@router.get(
    "/{operation_id}",
    response_model=InteractionOperationStatus,
    response_model_exclude_none=True,
)
async def get_interaction_operation(
    session_id: UUID,
    operation_id: UUID,
    principal: PrincipalDep,
    service: SessionServiceDep,
) -> InteractionOperationStatus:
    result = await service.get_interaction_operation(
        actor_from_http(principal), session_id, operation_id
    )
    return InteractionOperationStatus(
        operation_id=result.operation_id,
        status=result.status,
        result=(interaction_result_to_http(result.result) if result.result else None),
    )
