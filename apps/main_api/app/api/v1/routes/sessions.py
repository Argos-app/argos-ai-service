"""Rotas de sessões."""

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.v1.dependencies.auth import CreateSessionPrincipal, ReadSessionsPrincipal
from app.api.v1.dependencies.services import MessageHistoryServiceDep, SessionServiceDep
from app.api.v1.models.messages import (
    DEFAULT_MESSAGES_PAGE_SIZE,
    MAX_MESSAGES_PAGE_SIZE,
    SessionMessagesPage,
)
from app.api.v1.models.sessions import Session

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=Session, status_code=status.HTTP_201_CREATED)
async def create_session(principal: CreateSessionPrincipal, service: SessionServiceDep) -> Session:
    return await service.create_session(principal)


@router.get("", response_model=list[Session])
async def list_sessions(
    principal: ReadSessionsPrincipal, service: SessionServiceDep
) -> list[Session]:
    return await service.list_sessions(principal)


@router.get("/{session_id}/messages", response_model=SessionMessagesPage)
async def list_session_messages(
    session_id: UUID,
    principal: ReadSessionsPrincipal,
    service: MessageHistoryServiceDep,
    limit: int = Query(default=DEFAULT_MESSAGES_PAGE_SIZE, ge=1, le=MAX_MESSAGES_PAGE_SIZE),
    cursor: str | None = Query(default=None, min_length=1, max_length=2048),
) -> SessionMessagesPage:
    return await service.list_messages(
        principal,
        session_id,
        limit=limit,
        cursor=cursor,
    )
