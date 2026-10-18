"""Dependências de identidade e autenticação HTTP."""

import os
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel


class Principal(BaseModel):
    """Identidade autenticada, alinhada ao CurrentUserData do backend."""

    user_id: UUID
    email: str
    name: str
    permission: str
    farm_ids: list[UUID]


def _local_stub_principal() -> Principal:
    """Usuário fixo de desenvolvimento; não valida nem representa um token real."""
    return Principal(
        user_id=UUID("00000000-0000-4000-8000-000000000001"),
        email="usuario.teste@argos.local",
        name="Usuário de teste",
        permission="USER",
        farm_ids=[
            UUID("00000000-0000-4000-8000-000000000101"),
            UUID("00000000-0000-4000-8000-000000000102"),
        ],
    )


def get_principal(request: Request) -> Principal:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer obrigatório")
    app_env = os.getenv("APP_ENV", "")
    auth_mode = os.getenv("AUTH_MODE", "disabled")
    if app_env == "local" and auth_mode == "stub":
        return _local_stub_principal()

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Validação de autenticação não configurada",
    )


PrincipalDep = Annotated[Principal, Depends(get_principal)]
