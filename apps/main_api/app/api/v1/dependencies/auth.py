"""Valida identidade delegada pelo backend nas rotas HTTP."""

import base64
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.application.identity import ActorContext
from app.settings import Settings
from packages.observability import bind_user_id

_TOKEN_PART = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_TOKEN_BYTES = 16_384
_LOGGER = logging.getLogger(__name__)
_ALLOWED_OPERATIONS = frozenset(
    {
        "ai.sessions.create",
        "ai.sessions.read",
        "ai.interactions.create",
    }
)


class IdentityTokenConfigurationError(ValueError):
    """O validador não tem configuração suficiente para validar tokens."""


class InvalidIdentityToken(ValueError):
    """Token inválido, expirado ou incompatível com o contrato do backend."""


class Principal(BaseModel):
    """Identidade assinada pelo backend e usada para autorizar a requisição."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    email: str = Field(min_length=1)
    name: str = Field(min_length=1)
    permission: str = Field(min_length=1)
    farm_ids: tuple[UUID, ...] = ()
    operations: frozenset[str] = frozenset()
    request_id: UUID | None = None


def to_actor_context(principal: Principal) -> ActorContext:
    """Adapta a identidade HTTP ao contexto mínimo exigido pelos casos de uso."""
    return ActorContext(user_id=principal.user_id, farm_ids=principal.farm_ids)


def _decode_segment(value: str) -> bytes:
    if not value or not _TOKEN_PART.fullmatch(value):
        raise InvalidIdentityToken("token inválido")
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except ValueError as exc:
        raise InvalidIdentityToken("token inválido") from exc


def _decode_object(value: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise InvalidIdentityToken("token inválido")
            result[key] = item
        return result

    try:
        result = json.loads(value, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise InvalidIdentityToken("token inválido") from exc
    if not isinstance(result, dict):
        raise InvalidIdentityToken("token inválido")
    return result


class IdentityTokenVerifier:
    """Valida JWT EdDSA do backend sem aceitar algoritmos escolhidos pelo token."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not settings.auth_trusted_public_keys:
            raise IdentityTokenConfigurationError(
                "MAIN_API_AUTH_TRUSTED_PUBLIC_KEYS não está configurado"
            )
        if not settings.auth_issuer.strip() or not settings.auth_audience.strip():
            raise IdentityTokenConfigurationError("issuer e audience são obrigatórios")
        self._issuer = settings.auth_issuer
        self._audience = settings.auth_audience
        self._max_ttl_seconds = settings.auth_token_max_ttl_seconds
        self._clock_skew_seconds = settings.auth_clock_skew_seconds
        self._clock = clock
        self._keys = {
            key_id: self._load_key(encoded_key)
            for key_id, encoded_key in settings.auth_trusted_public_keys
        }

    @staticmethod
    def _load_key(value: str) -> Ed25519PublicKey:
        try:
            raw = base64.b64decode(value, validate=True)
            if len(raw) != 32:
                raise ValueError
            return Ed25519PublicKey.from_public_bytes(raw)
        except (ValueError, TypeError) as exc:
            raise IdentityTokenConfigurationError(
                "chave pública deve ser Ed25519 Base64 de 32 bytes"
            ) from exc

    def verify(self, token: str) -> Principal:
        if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise InvalidIdentityToken("token inválido")
        parts = token.split(".")
        if len(parts) != 3:
            raise InvalidIdentityToken("token inválido")

        header = _decode_object(_decode_segment(parts[0]))
        if header.get("alg") != "EdDSA" or "crit" in header or "b64" in header:
            raise InvalidIdentityToken("token inválido")
        key_id = header.get("kid")
        if not isinstance(key_id, str) or key_id not in self._keys:
            raise InvalidIdentityToken("token inválido")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        try:
            self._keys[key_id].verify(_decode_segment(parts[2]), signing_input)
        except (InvalidSignature, ValueError) as exc:
            raise InvalidIdentityToken("token inválido") from exc

        return self._principal_from_claims(_decode_object(_decode_segment(parts[1])))

    def _principal_from_claims(self, claims: dict[str, Any]) -> Principal:
        now = int(self._clock())
        issued_at = claims.get("iat")
        not_before = claims.get("nbf", issued_at)
        expires_at = claims.get("exp")
        if any(type(value) is not int for value in (issued_at, not_before, expires_at)):
            raise InvalidIdentityToken("token inválido")
        if (
            issued_at > now + self._clock_skew_seconds
            or not_before > now + self._clock_skew_seconds
        ):
            raise InvalidIdentityToken("token ainda não é válido")
        if (
            expires_at <= now
            or expires_at <= issued_at
            or not_before < issued_at
            or not_before > expires_at
            or expires_at - issued_at > self._max_ttl_seconds
        ):
            raise InvalidIdentityToken("token expirado ou com validade inválida")

        if claims.get("iss") != self._issuer:
            raise InvalidIdentityToken("token inválido")
        audience = claims.get("aud")
        if audience != self._audience and audience != [self._audience]:
            raise InvalidIdentityToken("token inválido")
        if claims.get("v") != 1 or type(claims.get("v")) is not int:
            raise InvalidIdentityToken("token inválido")
        if claims.get("agent") != "argos-ai-service":
            raise InvalidIdentityToken("token inválido")

        operations = claims.get("operations")
        if (
            not isinstance(operations, list)
            or not operations
            or any(not isinstance(item, str) for item in operations)
            or len(set(operations)) != len(operations)
            or not set(operations) <= _ALLOWED_OPERATIONS
        ):
            raise InvalidIdentityToken("token inválido")

        try:
            subject = claims["sub"]
            email = claims["email"]
            name = claims["name"]
            permission = claims["permission"]
            token_id = claims["jti"]
            request_id = UUID(claims["request_id"])
            if not isinstance(subject, str) or not all(
                isinstance(value, str) and value.strip()
                for value in (email, name, permission, token_id)
            ):
                raise ValueError
            if len(token_id) > 128:
                raise ValueError
            UUID(token_id)
            user_id = UUID(subject)
            farm_ids_raw = claims.get("farm_ids", [])
            if (
                not isinstance(farm_ids_raw, list)
                or len(farm_ids_raw) > 1000
                or not all(isinstance(value, str) for value in farm_ids_raw)
            ):
                raise ValueError
            farm_ids = tuple(UUID(value) for value in farm_ids_raw)
            if len(set(farm_ids)) != len(farm_ids):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidIdentityToken("token inválido") from exc

        return Principal(
            user_id=user_id,
            email=email,
            name=name,
            permission=permission,
            farm_ids=farm_ids,
            operations=frozenset(operations),
            request_id=request_id,
        )


def _local_stub_principal() -> Principal:
    """Usuário fixo para desenvolvimento local, sem representar token real."""
    return Principal(
        user_id=UUID("00000000-0000-4000-8000-000000000001"),
        email="usuario.teste@argos.local",
        name="Usuário de teste",
        permission="USER",
        farm_ids=(
            UUID("00000000-0000-4000-8000-000000000101"),
            UUID("00000000-0000-4000-8000-000000000102"),
        ),
        operations=_ALLOWED_OPERATIONS,
    )


def _verify_principal(request: Request) -> Principal:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not separator or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer obrigatório",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if os.getenv("APP_ENV", "") == "local" and os.getenv("AUTH_MODE", "") == "stub":
        return _local_stub_principal()

    verifier = getattr(request.app.state, "identity_token_verifier", None)
    if verifier is None:
        try:
            verifier = IdentityTokenVerifier(request.app.state.settings)
        except IdentityTokenConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Validação de autenticação não configurada",
            ) from exc
        request.app.state.identity_token_verifier = verifier

    try:
        return verifier.verify(token)
    except InvalidIdentityToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_principal(request: Request) -> AsyncIterator[Principal]:
    principal = _verify_principal(request)
    counter = getattr(request.app.state, "user_usage_counter", None)
    if counter is not None:
        try:
            await counter.increment_call(principal.user_id)
        except Exception:
            _LOGGER.warning("Redis user call counter increment failed")
    with bind_user_id(principal.user_id):
        yield principal


def principal_with_operation(operation: str) -> Callable[..., Principal]:
    """Cria uma dependência que exige operação delegada pelo backend."""
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
