"""Transporte MCP autenticado e ferramentas de consulta detalhada."""

import hmac
from typing import Any

from argos_mcp_data.adapters.delegation import (
    DelegationContextMiddleware,
    DelegationTokenVerifier,
    get_access_context,
)
from argos_mcp_data.application.cattle_queries import CattleQueryService
from argos_mcp_data.domain.cattle import (
    ListAnimalLotHistoryRequest,
    ListApplicationAnimalsRequest,
    ListApplicationsRequest,
    ListExceptionAnimalsRequest,
    ListHerdsRequest,
    ListLotsRequest,
    ListOccurrencesRequest,
    ListPropertiesRequest,
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
    """Cria endpoint Streamable HTTP `/mcp` autenticado."""

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


def register_cattle_tools(server: Any, service: CattleQueryService) -> None:
    """Registra as consultas SQL detalhadas disponíveis ao agente Fazenda."""

    @server.tool()
    async def list_properties(payload: ListPropertiesRequest) -> dict[str, Any]:
        """Lista propriedades autorizadas com filtros opcionais de nome e situação."""
        result = await service.list_properties(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def list_herds(payload: ListHerdsRequest) -> dict[str, Any]:
        """Lista rebanhos por propriedade, com filtros de nome, raça e situação."""
        result = await service.list_herds(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def list_lots(payload: ListLotsRequest) -> dict[str, Any]:
        """Lista lotes com categoria, datas, situação e faixa de quantidade de animais."""
        result = await service.list_lots(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def list_exception_animals(payload: ListExceptionAnimalsRequest) -> dict[str, Any]:
        """Lista animais cadastrados na estrutura atual de exceções, com lote e situação."""
        result = await service.list_exception_animals(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def list_applications(payload: ListApplicationsRequest) -> dict[str, Any]:
        """Lista aplicações, medicamentos, doses, quantidades e retirada de carne."""
        result = await service.list_applications(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def list_application_animals(
        payload: ListApplicationAnimalsRequest,
    ) -> dict[str, Any]:
        """Lista detalhes por animal das aplicações individualizadas."""
        result = await service.list_application_animals(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def list_occurrences(payload: ListOccurrencesRequest) -> dict[str, Any]:
        """Lista ocorrências clínicas e sua situação para animais autorizados."""
        result = await service.list_occurrences(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def list_animal_lot_history(
        payload: ListAnimalLotHistoryRequest,
    ) -> dict[str, Any]:
        """Lista o histórico de entrada e saída dos animais em lotes."""
        result = await service.list_animal_lot_history(get_access_context(), payload)
        return result.model_dump(mode="json")


def create_http_app(server: Any, delegation_verifier: DelegationTokenVerifier) -> Any:
    """Combina o app MCP do SDK com o middleware de delegação por requisição."""

    return DelegationContextMiddleware(server.streamable_http_app(), delegation_verifier)
