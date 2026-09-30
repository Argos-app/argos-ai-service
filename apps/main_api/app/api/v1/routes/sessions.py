"""Rotas de sessões."""

from fastapi import APIRouter, status

from app.api.v1.dependencies.auth import PrincipalDep
from app.api.v1.dependencies.services import SessionServiceDep
from app.api.v1.models.sessions import Session

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=Session, status_code=status.HTTP_201_CREATED)
async def create_session(principal: PrincipalDep, service: SessionServiceDep) -> Session:
    return await service.create_session(principal)


@router.get("", response_model=list[Session])
async def list_sessions(principal: PrincipalDep, service: SessionServiceDep) -> list[Session]:
    return await service.list_sessions(principal)
