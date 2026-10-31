"""Rotas para enviar mensagens ou respostas a perguntas."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Path, status

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
    summary="Enviar interação para processamento",
    description=(
        "Aceita uma nova mensagem ou uma resposta a uma pergunta pendente. "
        "O processamento ocorre em segundo plano; consulte operation_id para acompanhar o estado."
    ),
    responses={
        202: {"description": "Interação aceita para processamento em segundo plano."},
        400: {"description": "Conteúdo da interação inválido."},
        401: {"description": "Token ausente, inválido ou expirado."},
        403: {"description": "A sessão não pertence ao escopo autorizado."},
        404: {"description": "Sessão ou pergunta não encontrada."},
        409: {"description": "Interação incompatível com o estado atual da sessão."},
        422: {"description": "Corpo ou parâmetro inválido."},
        503: {"description": "Serviço de sessão indisponível."},
    },
)
async def create_interaction_async(
    session_id: Annotated[
        UUID,
        Path(
            description="Identificador UUID da sessão que receberá a interação.",
            examples=["123e4567-e89b-12d3-a456-426614174000"],
        ),
    ],
    body: Annotated[
        InteractionInput,
        Body(
            openapi_examples={
                "mensagem": {
                    "summary": "Enviar uma mensagem",
                    "description": "Inicia uma interação com uma mensagem do usuário.",
                    "value": {
                        "kind": "message",
                        "client_message_id": "123e4567-e89b-12d3-a456-426614174001",
                        "message": "Quantos animais há no lote 12?",
                    },
                },
                "resposta": {
                    "summary": "Responder a uma pergunta pendente",
                    "description": (
                        "Envia uma resposta vinculada à pergunta e à interação originais."
                    ),
                    "value": {
                        "kind": "answer",
                        "interaction_id": "123e4567-e89b-12d3-a456-426614174002",
                        "question_id": "123e4567-e89b-12d3-a456-426614174003",
                        "answer": {"kind": "text", "text": "Lote 12"},
                    },
                },
            }
        ),
    ],
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
    summary="Consultar estado da interação",
    description="Consulta o processamento de uma interação aceita anteriormente.",
    responses={
        200: {"description": "Estado atual da interação e resultado, quando concluído."},
        401: {"description": "Token ausente, inválido ou expirado."},
        403: {"description": "A sessão não pertence ao escopo autorizado."},
        404: {"description": "Sessão ou interação não encontrada."},
        422: {"description": "Identificador inválido."},
        503: {"description": "Serviço de sessão indisponível."},
    },
)
async def get_interaction_operation(
    session_id: Annotated[
        UUID,
        Path(
            description="Identificador UUID da sessão.",
            examples=["123e4567-e89b-12d3-a456-426614174000"],
        ),
    ],
    operation_id: Annotated[
        UUID,
        Path(
            description="Identificador UUID retornado ao aceitar a interação.",
            examples=["123e4567-e89b-12d3-a456-426614174004"],
        ),
    ],
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
