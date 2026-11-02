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
    """Lê o cabeçalho já validado pelo middleware de transporte."""
    del context
    from mcp.server.lowlevel.server import request_ctx

    request = request_ctx.get().request
    authorization = request.headers.get("authorization") if request is not None else None
    parts = authorization.split(None, 1) if authorization else []
    return f"Bearer {parts[1]}" if len(parts) == 2 else None


def create_mcp_server(settings: Settings, service: McpPredictionServer) -> Any:
    """Cria Streamable HTTP em /mcp e publica a única ferramenta somente leitura."""

    settings.validate_runtime()
    try:
        from mcp.server.fastmcp import Context, FastMCP
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o serviço") from exc

    server = FastMCP(
        "argos-mcp-prediction",
        host=settings.mcp_host,
        port=settings.mcp_port,
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

    server._argos_service_token = settings.internal_service_token
    return server


def create_http_app(server: Any, delegation_verifier: DelegationTokenVerifier) -> Any:
    """Combina o app MCP com a delegação por requisição, como no MCP de dados."""
    from packages.security.mcp_bearer import McpBearerMiddleware

    return McpBearerMiddleware(
        DelegationContextMiddleware(server.streamable_http_app(), delegation_verifier),
        server._argos_service_token,
    )
