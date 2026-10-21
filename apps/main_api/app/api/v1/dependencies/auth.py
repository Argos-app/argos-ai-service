"""Validação do JWT próprio e autorização atual baseada no cadastro PostgreSQL."""

import logging
from collections.abc import AsyncIterator, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.application.identity import ActorContext
from app.domain.user_access import UserAccess
from app.infrastructure.access_tokens import AccessTokenClaims, InvalidAccessToken
from app.settings import Settings
from packages.observability import bind_user_id

_LOGGER = logging.getLogger(__name__)
_ALLOWED_OPERATIONS = frozenset(
    {
        "ai.sessions.create",
        "ai.sessions.read",
        "ai.interactions.create",
    }
)


class Principal(BaseModel):
    """Identidade carregada do cadastro e escopo autorizado desta requisição."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    firebase_uid: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=1)
    name: str = Field(min_length=1)
    permission: str = Field(min_length=1)
    farm_ids: tuple[UUID, ...] = ()
    operations: frozenset[str] = frozenset()
    request_id: UUID | None = None


def to_actor_context(principal: Principal) -> ActorContext:
    """Adapta a identidade HTTP ao contexto mínimo exigido pelos casos de uso."""
    return ActorContext(
        user_id=principal.user_id,
        farm_ids=principal.farm_ids,
        firebase_uid=principal.firebase_uid,
    )


def _local_stub_principal() -> Principal:
    """Usuário fixo para desenvolvimento local, sem representar token real."""
    return Principal(
        user_id=UUID("00000000-0000-4000-8000-000000000001"),
        firebase_uid="local-stub-user",
        email="usuario.teste@argos.local",
        name="Usuário de teste",
        permission="USER",
        farm_ids=(
            UUID("00000000-0000-4000-8000-000000000101"),
            UUID("00000000-0000-4000-8000-000000000102"),
        ),
        operations=_ALLOWED_OPERATIONS,
    )


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not separator or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer obrigatório",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def _verify_access_token(request: Request, token: str | None = None) -> AccessTokenClaims:
    """Valida exclusivamente tokens assinados pelo emissor próprio da API."""
    codec = getattr(request.app.state, "access_token_codec", None)
    if codec is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Validação do JWT próprio não configurada",
        )
    try:
        return codec.verify(token if token is not None else _bearer_token(request))
    except InvalidAccessToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _user_access_is_enabled(user: UserAccess) -> bool:
    if not user.active or not user.permission_active or not user.farms:
        return False
    if user.blocked_until is None:
        return True
    from datetime import UTC, datetime

    blocked_until = user.blocked_until
    if blocked_until.tzinfo is None:
        blocked_until = blocked_until.replace(tzinfo=UTC)
    return blocked_until <= datetime.now(UTC)


async def get_principal(request: Request) -> AsyncIterator[Principal]:
    token = _bearer_token(request)
    settings: Settings = request.app.state.settings
    codec = getattr(request.app.state, "access_token_codec", None)
    if codec is None and settings.app_env == "local" and settings.auth_mode == "stub":
        principal = _local_stub_principal()
    else:
        principal = await _principal_from_access_token(request, token)

    counter = getattr(request.app.state, "user_usage_counter", None)
    if counter is not None:
        try:
            await counter.increment_call(principal.user_id)
        except Exception:
            _LOGGER.warning("Redis user call counter increment failed")
    with bind_user_id(principal.user_id):
        yield principal


async def _principal_from_access_token(request: Request, token: str) -> Principal:
    """Valida JWT e cadastro atual antes de criar o Principal."""
    claims = _verify_access_token(request, token)
    repository = getattr(request.app.state, "user_access_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Consulta de autorização não configurada",
        )
    try:
        user = await repository.find_by_firebase_uid(claims.firebase_uid)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível consultar o cadastro de acesso",
        ) from exc
    if user is None or user.firebase_uid != claims.firebase_uid or user.user_id != claims.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identidade Firebase não corresponde ao cadastro",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _user_access_is_enabled(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário sem cadastro habilitado ou sem fazendas autorizadas",
        )
    return Principal(
        user_id=user.user_id,
        firebase_uid=claims.firebase_uid,
        email=user.email,
        name=user.name,
        permission=user.permission_code,
        farm_ids=tuple(farm.farm_id for farm in user.farms),
        operations=_ALLOWED_OPERATIONS,
    )


def principal_with_operation(operation: str) -> Callable[..., Principal]:
    """Cria uma dependência que exige operação comum da API."""
    if operation not in _ALLOWED_OPERATIONS:
        raise ValueError(f"operação desconhecida: {operation}")

    async def require_operation(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if operation not in principal.operations:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operação não autorizada",
            )
        return principal

    return require_operation


PrincipalDep = Annotated[Principal, Depends(get_principal)]
CreateSessionPrincipal = Annotated[
    Principal,
    Depends(principal_with_operation("ai.sessions.create")),
]
ReadSessionsPrincipal = Annotated[
    Principal,
    Depends(principal_with_operation("ai.sessions.read")),
]
CreateInteractionPrincipal = Annotated[
    Principal,
    Depends(principal_with_operation("ai.interactions.create")),
]
