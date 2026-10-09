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


class StaticServiceTokenVerifier:
    """Valida o bearer token de serviço antes de o MCP executar uma tool."""

    def __init__(self, expected_token: str, resource_server_url: str) -> None:
        self._expected_token = expected_token
        self._resource_server_url = resource_server_url

    async def verify_token(self, token: str) -> Any | None:
        if not hmac.compare_digest(token, self._expected_token):
            return None
        from mcp.server.auth.provider import AccessToken

        return AccessToken(
            token=token,
            client_id="argos-main-service",
            scopes=["email:reports"],
            resource=self._resource_server_url,
        )


def _authenticated_token_from_context(context: Any) -> str | None:
    """Obtém somente o token que o middleware MCP já validou no transporte."""

    del context  # Context faz o SDK injetar o escopo da chamada; a auth fica no contextvar.
    from mcp.server.auth.middleware.auth_context import get_access_token

    access_token = get_access_token()
    return access_token.token if access_token is not None else None


def _authorization_from_context(context: Any) -> str | None:
    token = _authenticated_token_from_context(context)
    return f"Bearer {token}" if token is not None else None


def create_mcp_server(service: McpEmailServer, settings: Settings) -> Any:
    """Registra a facade no SDK MCP quando ele estiver instalado."""

    try:
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.fastmcp import Context, FastMCP
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o servidor") from exc

    if not settings.internal_service_token:
        raise ValueError("MCP_EMAIL_INTERNAL_SERVICE_TOKEN é obrigatório")
    if not settings.mcp_auth_issuer_url:
        raise ValueError("MCP_EMAIL_AUTH_ISSUER_URL é obrigatório")
    if not settings.mcp_resource_server_url:
        raise ValueError("MCP_EMAIL_RESOURCE_SERVER_URL é obrigatório")

    mcp = FastMCP(
        "argos-mcp-email",
        host=settings.mcp_host,
        port=settings.mcp_port,
        auth=AuthSettings(
            issuer_url=settings.mcp_auth_issuer_url,
            resource_server_url=settings.mcp_resource_server_url,
            validate_token_resource=True,
            required_scopes=["email:reports"],
        ),
        token_verifier=StaticServiceTokenVerifier(
            settings.internal_service_token,
            settings.mcp_resource_server_url,
        ),
    )

    @mcp.tool()
    async def prepare_report(payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Valida e prepara um relatório para os destinatários informados."""

        return await service.prepare_report_tool(
            payload,
            authorization=_authorization_from_context(ctx),
        )

    @mcp.tool()
    async def send_report(payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Envia um relatório previamente preparado."""

        return await service.send_report_tool(
            payload,
            authorization=_authorization_from_context(ctx),
        )

    @mcp.tool()
    async def get_report_status(payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Consulta o estado de uma operação de envio."""

        return await service.get_report_status_tool(
            payload,
            authorization=_authorization_from_context(ctx),
        )

    return mcp
