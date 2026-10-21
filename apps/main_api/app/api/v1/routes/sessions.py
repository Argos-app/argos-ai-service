"""Rotas de sessões."""

from uuid import UUID

from fastapi import APIRouter, Query, status

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


@router.post("", response_model=Session, status_code=status.HTTP_201_CREATED)
async def create_session(principal: PrincipalDep, service: SessionServiceDep) -> Session:
    result = await service.create_session(actor_from_http(principal))
    return session_to_http(result)


@router.get("", response_model=list[Session])
async def list_sessions(principal: PrincipalDep, service: SessionServiceDep) -> list[Session]:
    result = await service.list_sessions(actor_from_http(principal))
    return sessions_to_http(result)


@router.get("/{session_id}/messages", response_model=SessionMessagesPage)
async def list_session_messages(
    session_id: UUID,
    principal: PrincipalDep,
    service: MessageHistoryServiceDep,
    limit: int = Query(default=DEFAULT_MESSAGES_PAGE_SIZE, ge=1, le=MAX_MESSAGES_PAGE_SIZE),
    cursor: str | None = Query(default=None, min_length=1, max_length=2048),
) -> SessionMessagesPage:
    result = await service.list_messages(
        actor_from_http(principal), session_id, limit=limit, cursor=cursor
    )
    return message_page_to_http(result)
