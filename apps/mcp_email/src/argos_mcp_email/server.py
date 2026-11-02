"""Adapter de entrada MCP para as tools do serviço de e-mail."""

import hmac
from collections.abc import Mapping
from typing import Any

from argos_mcp_email.application import GetReportStatus, PrepareReport, SendReport
from argos_mcp_email.settings import Settings
from packages.contracts import PrepareReportRequest, ReportStatusRequest, SendReportRequest


class ServiceAuthenticationError(Exception):
    """Credencial ausente ou inválida para uma chamada entre serviços."""


class ServiceAuthenticator:
    def __init__(self, expected_token: str | None) -> None:
        self._expected_token = expected_token

    def verify(self, authorization: str | None) -> None:
        if not self._expected_token:
            raise ServiceAuthenticationError("autenticação interna não configurada")
        expected_authorization = f"Bearer {self._expected_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected_authorization):
            raise ServiceAuthenticationError("credencial de serviço inválida")


class McpEmailServer:
    """Facade autenticada usada pelo transporte MCP."""

    def __init__(
        self,
        prepare_report: PrepareReport,
        settings: Settings,
        send_report: SendReport | None = None,
        get_report_status: GetReportStatus | None = None,
    ) -> None:
        self._prepare_report = prepare_report
        self._send_report = send_report
        self._get_report_status = get_report_status
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

    async def send_report_tool(
        self, payload: Mapping[str, Any], *, authorization: str | None
    ) -> dict[str, Any]:
        self._authenticator.verify(authorization)
        if self._send_report is None:
            raise RuntimeError("tool de envio não configurada")
        request = SendReportRequest.model_validate(payload)
        return (await self._send_report.execute(request)).model_dump(mode="json")

    async def get_report_status_tool(
        self, payload: Mapping[str, Any], *, authorization: str | None
    ) -> dict[str, Any]:
        self._authenticator.verify(authorization)
        if self._get_report_status is None:
            raise RuntimeError("tool de consulta de estado não configurada")
        request = ReportStatusRequest.model_validate(payload)
        return (await self._get_report_status.execute(request)).model_dump(mode="json")


def _authenticated_token_from_context(context: Any) -> str | None:
    """Lê o cabeçalho já validado pelo middleware de transporte."""
    del context
    from mcp.server.lowlevel.server import request_ctx

    request = request_ctx.get().request
    authorization = request.headers.get("authorization") if request is not None else None
    parts = authorization.split(None, 1) if authorization else []
    return f"Bearer {parts[1]}" if len(parts) == 2 else None


def _authorization_from_context(context: Any) -> str | None:
    return _authenticated_token_from_context(context)


def create_mcp_server(settings: Settings) -> Any:
    """Cria o transporte MCP autenticado sem registrar ferramentas."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o servidor") from exc

    if not settings.internal_service_token:
        raise ValueError("MCP_EMAIL_INTERNAL_SERVICE_TOKEN é obrigatório")

    return FastMCP(
        "argos-mcp-email",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )


def register_email_tools(server: Any, service: McpEmailServer) -> None:
    """Registra as ferramentas de e-mail no servidor MCP informado."""

    try:
        from mcp.server.fastmcp import Context
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o servidor") from exc

    @server.tool()
    async def prepare_report(payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Valida e prepara um relatório para os destinatários informados."""

        return await service.prepare_report_tool(
            payload,
            authorization=_authorization_from_context(ctx),
        )

    @server.tool()
    async def send_report(payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Envia um relatório previamente preparado."""

        return await service.send_report_tool(
            payload,
            authorization=_authorization_from_context(ctx),
        )

    @server.tool()
    async def get_report_status(payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Consulta o estado de uma operação de envio."""

        return await service.get_report_status_tool(
            payload,
            authorization=_authorization_from_context(ctx),
        )
