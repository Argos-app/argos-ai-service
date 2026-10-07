"""Transporte MCP autenticado sem ferramentas de negócio nesta etapa."""

import hmac
from typing import Any

from argos_mcp_data.adapters.delegation import (
    DelegationContextMiddleware,
    DelegationTokenVerifier,
)
from argos_mcp_data.settings import Settings


class StaticServiceTokenVerifier:
    """Verifica token de serviço no transporte; delegação de usuário é etapa 3."""

    def __init__(self, expected_token: str, resource_server_url: str) -> None:
        self._expected_token = expected_token
        self._resource_server_url = resource_server_url

    def is_expected_token(self, token: str) -> bool:
        return hmac.compare_digest(token, self._expected_token)

    async def verify_token(self, token: str) -> Any | None:
        if not self.is_expected_token(token):
            return None
        from mcp.server.auth.provider import AccessToken

        return AccessToken(
            token=token,
            client_id="argos-ai-service",
            scopes=["data:read"],
            resource=self._resource_server_url,
        )


def create_mcp_server(settings: Settings) -> Any:
    """Cria endpoint Streamable HTTP `/mcp`, sem registrar tools nesta etapa."""

    settings.validate_runtime()
    try:
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o serviço") from exc

    return FastMCP(
        "argos-mcp-data",
        host=settings.mcp_host,
        port=settings.mcp_port,
        auth=AuthSettings(
            issuer_url=settings.mcp_auth_issuer_url,
            resource_server_url=settings.mcp_resource_server_url,
            validate_token_resource=True,
            required_scopes=["data:read"],
        ),
        token_verifier=StaticServiceTokenVerifier(
            settings.internal_service_token,
            settings.mcp_resource_server_url,
        ),
    )


def create_http_app(server: Any, delegation_verifier: DelegationTokenVerifier) -> Any:
    """Combina o app MCP do SDK com o middleware de delegação por requisição."""

    return DelegationContextMiddleware(server.streamable_http_app(), delegation_verifier)
