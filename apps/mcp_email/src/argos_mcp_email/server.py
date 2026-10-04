"""Adapter de entrada MCP para as tools do serviço de e-mail."""

from collections.abc import Mapping
from typing import Any

from argos_mcp_email.application import PrepareReport
from argos_mcp_email.settings import Settings
from packages.contracts import PrepareReportRequest


class ServiceAuthenticationError(Exception):
    """Credencial ausente ou inválida para uma chamada entre serviços."""


class ServiceAuthenticator:
    def __init__(self, expected_token: str | None) -> None:
        self._expected_token = expected_token

    def verify(self, authorization: str | None) -> None:
        if not self._expected_token:
            raise ServiceAuthenticationError("autenticação interna não configurada")
        if authorization != f"Bearer {self._expected_token}":
            raise ServiceAuthenticationError("credencial de serviço inválida")


class McpEmailServer:
    """Facade autenticada usada pelo transporte MCP."""

    def __init__(self, prepare_report: PrepareReport, settings: Settings) -> None:
        self._prepare_report = prepare_report
        self._authenticator = ServiceAuthenticator(settings.internal_service_token)

    async def prepare_report_tool(
        self,
        payload: Mapping[str, Any],
        *,
        authorization: str | None,
    ) -> dict[str, Any]:
        self._authenticator.verify(authorization)
        request = PrepareReportRequest.model_validate(payload)
        return (await self._prepare_report.execute(request)).model_dump(mode="json")


def create_mcp_server(service: McpEmailServer) -> Any:
    """Registra a facade no SDK MCP quando ele estiver instalado."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o servidor") from exc

    mcp = FastMCP("argos-mcp-email")

    @mcp.tool()
    async def prepare_report(payload: dict[str, Any], authorization: str) -> dict[str, Any]:
        """Valida e prepara um relatório para os destinatários informados."""

        return await service.prepare_report_tool(payload, authorization=authorization)

    return mcp
