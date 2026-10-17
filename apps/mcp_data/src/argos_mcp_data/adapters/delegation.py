"""Validação de atestação Ed25519 e contexto por chamada HTTP/MCP."""

import base64
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mcp.server.lowlevel.server import request_ctx
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from argos_mcp_data.application import AccessPolicy
from argos_mcp_data.domain import AccessContext, AuthorizedScope
from argos_mcp_data.domain.errors import AccessContextError

_ACCESS_CONTEXT_KEY = "argos_mcp_data_access_context"
_TOKEN_PART = re.compile(r"^[A-Za-z0-9_-]+$")
_OPERATION = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_AGENTS = {"fazenda", "doutor", "analista"}
_AGENT_OPERATIONS = {
    "fazenda": {"cattle.read", "medicine.read", "report.read"},
    "doutor": {"medicine.read"},
    "analista": {"cattle.read"},
}
_MAX_TOKEN_BYTES = 16_384


def _b64url_decode(value: str) -> bytes:
    if not _TOKEN_PART.fullmatch(value):
        raise AccessContextError("contexto delegado inválido")
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except ValueError as exc:
        raise AccessContextError("contexto delegado inválido") from exc


def _json_object(data: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AccessContextError("contexto delegado inválido")
            result[key] = value
        return result

    try:
        parsed = json.loads(data, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise AccessContextError("contexto delegado inválido") from exc
    if not isinstance(parsed, dict):
        raise AccessContextError("contexto delegado inválido")
    return parsed


class DelegationTokenVerifier:
    """Valida somente JWT EdDSA com chave pública configurada e audience do Data."""

    def __init__(
        self,
        trusted_keys: dict[str, str],
        *,
        issuer: str,
        audience: str,
        max_ttl_seconds: int = 300,
        clock_skew_seconds: int = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not trusted_keys:
            raise ValueError("ao menos uma chave pública de delegação é obrigatória")
        self._keys = {key_id: self._load_key(value) for key_id, value in trusted_keys.items()}
        self._issuer = issuer
        self._audience = audience
        self._max_ttl_seconds = max_ttl_seconds
        self._clock_skew_seconds = clock_skew_seconds
        self._clock = clock

    @staticmethod
    def _load_key(value: str) -> Ed25519PublicKey:
        try:
            raw = base64.b64decode(value, validate=True)
            if len(raw) != 32:
                raise ValueError
            return Ed25519PublicKey.from_public_bytes(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "chave pública de delegação deve ser Ed25519 Base64 de 32 bytes"
            ) from exc

    def verify(self, token: str) -> AccessContext:
        if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise AccessContextError("contexto delegado inválido")
        parts = token.split(".")
        if len(parts) != 3:
            raise AccessContextError("contexto delegado inválido")
        header = _json_object(_b64url_decode(parts[0]))
        if header.get("alg") != "EdDSA" or "crit" in header or "b64" in header:
            raise AccessContextError("contexto delegado inválido")
        key_id = header.get("kid")
        if not isinstance(key_id, str) or key_id not in self._keys:
            raise AccessContextError("contexto delegado inválido")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        try:
            self._keys[key_id].verify(_b64url_decode(parts[2]), signing_input)
        except (InvalidSignature, ValueError) as exc:
            raise AccessContextError("contexto delegado inválido") from exc

        claims = _json_object(_b64url_decode(parts[1]))
        return self._parse_claims(claims)

    def _parse_claims(self, claims: dict[str, Any]) -> AccessContext:
        now = int(self._clock())
        issued_at = claims.get("iat")
        not_before = claims.get("nbf", issued_at)
        expires_at = claims.get("exp")
        if any(type(value) is not int for value in (issued_at, not_before, expires_at)):
            raise AccessContextError("contexto delegado inválido")
        if (
            issued_at > now + self._clock_skew_seconds
            or not_before > now + self._clock_skew_seconds
        ):
            raise AccessContextError("contexto delegado inválido")
        if expires_at <= now or expires_at <= issued_at or not_before > expires_at:
            raise AccessContextError("contexto delegado expirado")
        if expires_at - issued_at > self._max_ttl_seconds:
            raise AccessContextError("contexto delegado inválido")
        if claims.get("iss") != self._issuer:
            raise AccessContextError("contexto delegado inválido")
        audience = claims.get("aud")
        if audience != self._audience and audience != [self._audience]:
            raise AccessContextError("contexto delegado inválido")
        if claims.get("v") != 1 or type(claims.get("v")) is not int:
            raise AccessContextError("contexto delegado inválido")

        try:
            subject = claims["sub"]
            if not isinstance(subject, str):
                raise ValueError
            user_id = UUID(subject)
            properties_raw = claims["authorized_property_ids"]
            if not isinstance(properties_raw, list) or not 1 <= len(properties_raw) <= 1000:
                raise ValueError
            if not all(isinstance(value, str) for value in properties_raw):
                raise ValueError
            properties = tuple(UUID(value) for value in properties_raw)
            if len(set(properties)) != len(properties):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise AccessContextError("contexto delegado inválido") from exc

        agent_id = claims.get("agent")
        operations_raw = claims.get("operations")
        if not isinstance(agent_id, str) or agent_id not in _AGENTS:
            raise AccessContextError("contexto delegado inválido")
        if (
            not isinstance(operations_raw, list)
            or not 1 <= len(operations_raw) <= 64
            or not all(
                isinstance(item, str) and _OPERATION.fullmatch(item) for item in operations_raw
            )
            or len(set(operations_raw)) != len(operations_raw)
        ):
            raise AccessContextError("contexto delegado inválido")
        if not set(operations_raw).issubset(_AGENT_OPERATIONS[agent_id]):
            raise AccessContextError("contexto delegado inválido")
        token_id = claims.get("jti")
        request_id = claims.get("request_id")
        if not self._valid_identifier(token_id) or not self._valid_identifier(request_id):
            raise AccessContextError("contexto delegado inválido")

        return AccessContext(
            user_id=user_id,
            authorized_property_ids=properties,
            operations=frozenset(operations_raw),
            agent_id=agent_id,
            issued_at=datetime.fromtimestamp(issued_at, UTC),
            expires_at=datetime.fromtimestamp(expires_at, UTC),
            token_id=token_id,
            request_id=request_id,
            correlation_id=self._optional_uuid_claim(claims, "correlation_id"),
            parent_observation_id=self._optional_uuid_claim(claims, "parent_observation_id"),
        )

    @staticmethod
    def _optional_uuid_claim(claims: dict[str, Any], name: str) -> UUID | None:
        value = claims.get(name)
        if value is None:
            return None
        try:
            parsed = UUID(value) if isinstance(value, str) else None
        except ValueError as exc:
            raise AccessContextError("contexto delegado inválido") from exc
        if parsed is None or str(parsed) != value.lower():
            raise AccessContextError("contexto delegado inválido")
        return parsed

    @staticmethod
    def _valid_identifier(value: Any) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= 128


def get_access_context() -> AccessContext:
    # O SDK transporta a requisição HTTP atual nos metadados de cada mensagem.
    # Um ContextVar próprio seria herdado da inicialização da sessão MCP e
    # manteria uma delegação antiga nas chamadas seguintes da mesma sessão.
    try:
        request = request_ctx.get().request
    except LookupError as exc:
        raise AccessContextError("contexto de acesso obrigatório") from exc
    if request is None:
        raise AccessContextError("contexto de acesso obrigatório")
    context = request.scope.get("state", {}).get(_ACCESS_CONTEXT_KEY)
    if not isinstance(context, AccessContext):
        raise AccessContextError("contexto de acesso obrigatório")
    return context


async def authorize_mcp_call(
    access_policy: AccessPolicy,
    *,
    operation: str,
    selected_property_ids: tuple[UUID, ...] | None = None,
) -> AuthorizedScope:
    """Obtém contexto somente do middleware e revalida-o antes da consulta."""

    return await access_policy.authorize(
        get_access_context(),
        operation=operation,
        selected_property_ids=selected_property_ids,
    )


class DelegationContextMiddleware:
    """Exige e valida contexto do usuário antes de qualquer chamada a `/mcp`."""

    def __init__(self, app: ASGIApp, verifier: DelegationTokenVerifier) -> None:
        self._app = app
        self._verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/mcp":
            await self._app(scope, receive, send)
            return
        values = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"x-argos-delegation"
        ]
        if len(values) != 1:
            await JSONResponse(
                {"detail": "contexto de acesso obrigatório ou inválido"}, status_code=401
            )(scope, receive, send)
            return
        try:
            token = values[0].decode("ascii")
            context = self._verifier.verify(token)
        except (UnicodeDecodeError, AccessContextError):
            await JSONResponse(
                {"detail": "contexto de acesso obrigatório ou inválido"}, status_code=401
            )(scope, receive, send)
            return

        # Copiar o estado evita compartilhar identidade entre requisições.
        scoped_request = {
            **scope,
            "state": {**scope.get("state", {}), _ACCESS_CONTEXT_KEY: context},
        }
        await self._app(scoped_request, receive, send)
