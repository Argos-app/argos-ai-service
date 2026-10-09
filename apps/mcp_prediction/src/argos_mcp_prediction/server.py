"""Servidor MCP autenticado, ainda sem ferramentas de negócio."""

import hmac
from typing import Any

from argos_mcp_prediction.adapters.delegation import DelegationContextMiddleware
from argos_mcp_prediction.settings import Settings
from packages.security.delegation import DelegationTokenVerifier


class StaticServiceTokenVerifier:
    """Autentica somente o serviço chamador; delegação do usuário é validada pelo middleware."""

    def __init__(self, expected_token: str, resource_server_url: str) -> None:
        if not expected_token:
            raise ValueError("token interno de serviço é obrigatório")
        self._expected_token = expected_token
        self._resource_server_url = resource_server_url

    def is_expected_token(self, token: str) -> bool:
        return hmac.compare_digest(token.encode("utf-8"), self._expected_token.encode("utf-8"))

    async def verify_token(self, token: str) -> Any | None:
        if not self.is_expected_token(token):
            return None
        from mcp.server.auth.provider import AccessToken

        return AccessToken(
            token=token,
            client_id="argos-main-service",
            scopes=["prediction:read"],
            resource=self._resource_server_url,
        )


def create_mcp_server(settings: Settings) -> Any:
    """Cria Streamable HTTP em /mcp, com Bearer interno e catálogo vazio."""

    settings.validate_runtime()
    try:
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o serviço") from exc

    return FastMCP(
        "argos-mcp-prediction",
        host=settings.mcp_host,
        port=settings.mcp_port,
        auth=AuthSettings(
            issuer_url=settings.mcp_auth_issuer_url,
            resource_server_url=settings.mcp_resource_server_url,
            validate_token_resource=True,
            required_scopes=["prediction:read"],
        ),
        token_verifier=StaticServiceTokenVerifier(
            settings.internal_service_token,
            settings.mcp_resource_server_url,
        ),
    )


def create_http_app(server: Any, delegation_verifier: DelegationTokenVerifier) -> Any:
    """Combina o app MCP com a delegação por requisição, como no MCP de dados."""
    return DelegationContextMiddleware(server.streamable_http_app(), delegation_verifier)
