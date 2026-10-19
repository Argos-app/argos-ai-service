"""Emite atestações Ed25519 curtas para chamadas aos MCPs com escopo de propriedade."""

import base64
import json
import time
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.settings import Settings
from packages.observability import current_correlation_id, current_parent_observation_id

_AGENT_OPERATIONS = {
    "data": {
        "fazenda": frozenset({"cattle.read", "medicine.read", "report.read"}),
        "doutor": frozenset({"medicine.read"}),
        "analista": frozenset({"cattle.read"}),
    },
    "prediction": {"analista": frozenset({"prediction.read"})},
}


class DelegationTokenConfigurationError(ValueError):
    """Configuração da chave emissora inválida ou incompleta."""


class DelegationRequestError(ValueError):
    """Solicitação de delegação fora da política estática dos MCPs."""


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class McpDelegationTokenIssuer:
    """Assina um token por operação com audiência, agente e escopo explícitos."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not settings.mcp_delegation_issuer.strip():
            raise DelegationTokenConfigurationError("issuer da delegação é obrigatório")
        if not settings.mcp_delegation_key_id or not settings.mcp_delegation_private_key:
            raise DelegationTokenConfigurationError(
                "MAIN_API_MCP_DELEGATION_KEY_ID e "
                "MAIN_API_MCP_DELEGATION_PRIVATE_KEY são obrigatórios"
            )
        self._issuer = settings.mcp_delegation_issuer
        self._key_id = settings.mcp_delegation_key_id
        self._private_key = self._load_private_key(settings.mcp_delegation_private_key)
        self._ttl_seconds = settings.mcp_delegation_ttl_seconds
        self._audiences = {
            "data": settings.mcp_data_url,
            "prediction": settings.mcp_prediction_url,
        }
        self._clock = clock

    @staticmethod
    def _load_private_key(value: str) -> Ed25519PrivateKey:
        try:
            encoded = base64.b64decode(value, validate=True)
            key = serialization.load_der_private_key(encoded, password=None)
        except (ValueError, TypeError) as exc:
            raise DelegationTokenConfigurationError(
                "chave de delegação deve ser PKCS#8 DER Ed25519 codificada em Base64"
            ) from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise DelegationTokenConfigurationError("chave privada de delegação deve usar Ed25519")
        return key

    def issue(
        self,
        *,
        service: str,
        user_id: UUID | str,
        authorized_property_ids: Sequence[UUID | str],
        agent: str,
        operation: str,
        request_id: UUID | str,
    ) -> str:
        """Cria um JWT de uso curto; service/agent/operation devem vir do binding fixo da tool."""
        allowed_operations = _AGENT_OPERATIONS.get(service, {}).get(agent)
        if allowed_operations is None or operation not in allowed_operations:
            raise DelegationRequestError("agente ou operação não autorizados para este MCP")
        audience = self._audiences.get(service)
        if not audience:
            raise DelegationRequestError("audience do MCP não está configurada")

        try:
            subject = str(UUID(str(user_id)))
            request_identifier = str(request_id)
            properties = tuple(str(UUID(str(value))) for value in authorized_property_ids)
        except (TypeError, ValueError, AttributeError) as exc:
            raise DelegationRequestError("identidade ou propriedades inválidas") from exc
        if not 1 <= len(properties) <= 1000 or len(set(properties)) != len(properties):
            raise DelegationRequestError("escopo deve conter de 1 a 1000 propriedades únicas")
        if not 1 <= len(request_identifier) <= 128:
            raise DelegationRequestError("request_id inválido")

        issued_at = int(self._clock())
        header = {"alg": "EdDSA", "kid": self._key_id, "typ": "JWT"}
        claims: dict[str, Any] = {
            "v": 1,
            "iss": self._issuer,
            "aud": audience,
            "sub": subject,
            "authorized_property_ids": list(properties),
            "agent": agent,
            "operations": [operation],
            "request_id": request_identifier,
            "correlation_id": str(current_correlation_id() or request_identifier),
            "parent_observation_id": (
                str(current_parent_observation_id())
                if current_parent_observation_id() is not None
                else None
            ),
            "jti": str(uuid4()),
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + self._ttl_seconds,
        }
        encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
        encoded_claims = _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        signature = self._private_key.sign(signing_input)
        return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"

    def headers_for_call(self, **delegation: Any) -> dict[str, str]:
        """Retorna o header consumido pelos middlewares dos MCPs."""
        return {"X-Argos-Delegation": self.issue(**delegation)}
