"""Rotas de sessões."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from app.api.v1.dependencies.auth import PrincipalDep
from app.api.v1.dependencies.services import MessageHistoryServiceDep, SessionServiceDep
from app.api.v1.mappers import (
    actor_from_http,
    message_page_to_http,
    session_to_http,
    sessions_to_http,
)
from app.api.v1.models.messages import (
    DEFAULT_MESSAGES_PAGE_SIZE,
    MAX_MESSAGES_PAGE_SIZE,
    SessionMessagesPage,
)
from app.api.v1.models.sessions import Session

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "",
    response_model=Session,
    status_code=status.HTTP_201_CREATED,
    summary="Criar sessão",
    description="Cria uma sessão para o usuário autenticado e retorna seu identificador.",
    responses={
        201: {"description": "Sessão criada."},
        401: {"description": "Token ausente, inválido ou expirado."},
        403: {"description": "Usuário sem acesso habilitado."},
        503: {"description": "Persistência ou serviço de identidade indisponível."},
    },
)
async def create_session(principal: PrincipalDep, service: SessionServiceDep) -> Session:
    result = await service.create_session(actor_from_http(principal))
    return session_to_http(result)


@router.get(
    "",
    response_model=list[Session],
    summary="Listar sessões",
    description="Lista as sessões pertencentes ao usuário autenticado.",
    responses={
        200: {"description": "Sessões do usuário autenticado."},
        401: {"description": "Token ausente, inválido ou expirado."},
        403: {"description": "Usuário sem acesso habilitado."},
        503: {"description": "Persistência ou serviço de identidade indisponível."},
    },
)
async def list_sessions(principal: PrincipalDep, service: SessionServiceDep) -> list[Session]:
    result = await service.list_sessions(actor_from_http(principal))
    return sessions_to_http(result)


@router.get(
    "/{session_id}/messages",
    response_model=SessionMessagesPage,
    summary="Consultar mensagens da sessão",
    description=(
        "Retorna mensagens sanitizadas em ordem cronológica. Use next_cursor para buscar "
        "a próxima página."
    ),
    responses={
        200: {"description": "Página de mensagens da sessão."},
        401: {"description": "Token ausente, inválido ou expirado."},
        403: {"description": "A sessão não pertence ao escopo autorizado."},
        404: {"description": "Sessão não encontrada."},
        422: {"description": "Identificador ou parâmetro inválido."},
        503: {"description": "Histórico da sessão indisponível."},
    },
)
async def list_session_messages(
    session_id: Annotated[
        UUID,
        Path(
            description="Identificador UUID da sessão consultada.",
            examples=["123e4567-e89b-12d3-a456-426614174000"],
        ),
    ],
    principal: PrincipalDep,
    service: MessageHistoryServiceDep,
    limit: int = Query(
        default=DEFAULT_MESSAGES_PAGE_SIZE,
        ge=1,
        le=MAX_MESSAGES_PAGE_SIZE,
        description="Quantidade máxima de mensagens retornadas nesta página.",
        examples=[DEFAULT_MESSAGES_PAGE_SIZE],
    ),
    cursor: str | None = Query(
        default=None,
        min_length=1,
        max_length=2048,
        description="Cursor opaco retornado como next_cursor na página anterior.",
        examples=["cursor-opaco-retornado-pela-api"],
    ),
) -> SessionMessagesPage:
    result = await service.list_messages(
        actor_from_http(principal), session_id, limit=limit, cursor=cursor
    )
    return message_page_to_http(result)
