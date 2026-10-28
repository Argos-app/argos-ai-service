"""Transporte MCP autenticado e ferramenta de predição somente leitura."""

import hmac
from collections.abc import Mapping
from typing import Any

from argos_mcp_prediction.adapters.delegation import DelegationContextMiddleware
from argos_mcp_prediction.application import EstimateMedicineConsumption
from argos_mcp_prediction.settings import Settings
from packages.contracts import EstimateMedicineConsumptionRequest
from packages.security.access import AccessContext
from packages.security.delegation import DelegationTokenVerifier


class ServiceAuthenticationError(Exception):
    """Credencial ausente ou inválida para uma chamada entre serviços."""


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


class McpPredictionServer:
    """Facade da ferramenta de predição, autenticada como serviço chamador."""

    def __init__(
        self,
        estimate_consumption: EstimateMedicineConsumption,
        settings: Settings,
    ) -> None:
        self._estimate_consumption = estimate_consumption
        self._expected_token = settings.internal_service_token

    async def estimate_medicine_consumption_tool(
        self,
        payload: Mapping[str, Any],
        *,
        authorization: str | None,
        access_context: AccessContext,
    ) -> dict[str, Any]:
        """Valida payload e delegação antes de executar a leitura autorizada."""

        if not self._expected_token:
            raise ServiceAuthenticationError("autenticação interna não configurada")
        expected_authorization = f"Bearer {self._expected_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected_authorization):
            raise ServiceAuthenticationError("credencial de serviço inválida")
        request = EstimateMedicineConsumptionRequest.model_validate(payload)
        response = await self._estimate_consumption.execute(access_context, request)
        return response.model_dump(mode="json")


def _authorization_from_context(context: Any) -> str | None:
    """Obtém somente o token que o middleware MCP já validou no transporte.

    O SDK injeta o escopo da chamada em Context; a autenticação fica no contextvar."""
    del context
    from mcp.server.auth.middleware.auth_context import get_access_token

    access_token = get_access_token()
    return f"Bearer {access_token.token}" if access_token is not None else None


def create_mcp_server(settings: Settings, service: McpPredictionServer) -> Any:
    """Cria Streamable HTTP em /mcp e publica a única ferramenta somente leitura."""

    settings.validate_runtime()
    try:
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.fastmcp import Context, FastMCP
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o serviço") from exc

    server = FastMCP(
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

    @server.tool()
    async def estimate_medicine_consumption(
        payload: dict[str, Any], ctx: Context
    ) -> dict[str, Any]:
        """Estima o consumo histórico sazonal de medicamentos no escopo autorizado."""

        from argos_mcp_prediction.adapters.delegation import get_access_context

        return await service.estimate_medicine_consumption_tool(
            payload,
            authorization=_authorization_from_context(ctx),
            access_context=get_access_context(),
        )

    return server


def create_http_app(server: Any, delegation_verifier: DelegationTokenVerifier) -> Any:
    """Combina o app MCP com a delegação por requisição, como no MCP de dados."""
    return DelegationContextMiddleware(server.streamable_http_app(), delegation_verifier)
