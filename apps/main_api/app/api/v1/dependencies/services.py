"""Interfaces e injeção dos serviços da aplicação."""

from typing import Annotated, Protocol
from uuid import UUID

from fastapi import Depends, HTTPException

from app.api.v1.dependencies.auth import Principal
from app.api.v1.models.interactions import InteractionInput, InteractionResult
from app.api.v1.models.sessions import Session


class SessionService(Protocol):
    async def create_session(self, principal: Principal) -> Session: ...

    async def list_sessions(self, principal: Principal) -> list[Session]: ...

    async def create_interaction(
        self, principal: Principal, session_id: UUID, body: InteractionInput
    ) -> InteractionResult: ...


def get_session_service() -> SessionService:
    raise HTTPException(status_code=503, detail="Sessões indisponíveis")


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
