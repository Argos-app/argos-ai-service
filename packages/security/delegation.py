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

from packages.security.access import AccessContext, DocumentAccessContext
from packages.security.errors import AccessContextError

_TOKEN_PART = re.compile(r"^[A-Za-z0-9_-]+$")
_OPERATION = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
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
    """Valida somente JWT EdDSA com chave pública configurada e audience e permissões do serviço."""

    def __init__(
        self,
        trusted_keys: dict[str, str],
        *,
        issuer: str,
        audience: str,
        agent_operations: dict[str, frozenset[str]],
        max_ttl_seconds: int = 300,
        clock_skew_seconds: int = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not trusted_keys:
            raise ValueError("ao menos uma chave pública de delegação é obrigatória")
        self._keys = {key_id: self._load_key(value) for key_id, value in trusted_keys.items()}
        if not agent_operations or not 1 <= max_ttl_seconds <= 300:
            raise ValueError("política de delegação inválida")
        if not 0 <= clock_skew_seconds <= 60:
            raise ValueError("tolerância de relógio inválida")
        self._agent_operations = {
            agent: frozenset(operations) for agent, operations in agent_operations.items()
        }
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
        if not isinstance(agent_id, str) or agent_id not in self._agent_operations:
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
        if not set(operations_raw).issubset(self._agent_operations[agent_id]):
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


class GlobalDocumentDelegationTokenVerifier(DelegationTokenVerifier):
    """Verifica delegação global documental com a mesma assinatura Ed25519.

    Este contexto não transporta propriedades e não pode ser usado pelas
    políticas de acesso que exigem escopo de propriedade.
    """

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
        super().__init__(
            trusted_keys,
            issuer=issuer,
            audience=audience,
            agent_operations={"informacoes": frozenset({"documents.read"})},
            max_ttl_seconds=max_ttl_seconds,
            clock_skew_seconds=clock_skew_seconds,
            clock=clock,
        )

    def verify(self, token: str) -> DocumentAccessContext:
        # O método-base valida estrutura, assinatura, tamanho e claims comuns;
        # a sobrescrita de _parse_claims aplica o formato global sem propriedades.
        return super().verify(token)  # type: ignore[return-value]

    def _parse_claims(self, claims: dict[str, Any]) -> DocumentAccessContext:
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
        except (KeyError, TypeError, ValueError) as exc:
            raise AccessContextError("contexto delegado inválido") from exc

        agent_id = claims.get("agent")
        operations_raw = claims.get("operations")
        if not isinstance(agent_id, str) or agent_id not in self._agent_operations:
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
        if not set(operations_raw).issubset(self._agent_operations[agent_id]):
            raise AccessContextError("contexto delegado inválido")
        token_id = claims.get("jti")
        request_id = claims.get("request_id")
        if not self._valid_identifier(token_id) or not self._valid_identifier(request_id):
            raise AccessContextError("contexto delegado inválido")

        return DocumentAccessContext(
            user_id=user_id,
            operations=frozenset(operations_raw),
            agent_id=agent_id,
            issued_at=datetime.fromtimestamp(issued_at, UTC),
            expires_at=datetime.fromtimestamp(expires_at, UTC),
            token_id=token_id,
            request_id=request_id,
            correlation_id=self._optional_uuid_claim(claims, "correlation_id"),
            parent_observation_id=self._optional_uuid_claim(claims, "parent_observation_id"),
            session_id=self._optional_uuid_claim(claims, "session_id"),
            interaction_id=self._optional_uuid_claim(claims, "interaction_id"),
        )
