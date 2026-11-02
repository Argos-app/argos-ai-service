"""Transporte MCP autenticado e ferramentas de consulta detalhada."""

from typing import Any

from argos_mcp_data.adapters.delegation import (
    DelegationContextMiddleware,
    DelegationTokenVerifier,
    get_access_context,
)
from argos_mcp_data.application.cattle_queries import CattleQueryService
from argos_mcp_data.application.medicine_queries import MedicineQueryService
from argos_mcp_data.domain.cattle import (
    GetLotReportSourceRequest,
    ListAnimalLotHistoryRequest,
    ListApplicationAnimalsRequest,
    ListApplicationsRequest,
    ListExceptionAnimalsRequest,
    ListHerdsRequest,
    ListLotsRequest,
    ListOccurrencesRequest,
    ListPropertiesRequest,
)
from argos_mcp_data.domain.cattle_aggregates import (
    ExceptionSummaryRequest,
    FarmSummaryRequest,
)
from argos_mcp_data.domain.cattle_documents import ListCattleDocumentsRequest
from argos_mcp_data.domain.medicine import ListMedicineDocumentsRequest, ListMedicinesRequest
from argos_mcp_data.settings import Settings


def create_mcp_server(settings: Settings) -> Any:
    """Cria endpoint Streamable HTTP `/mcp` autenticado."""

    settings.validate_runtime()
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'mcp' para iniciar o serviço") from exc

    server = FastMCP(
        "argos-mcp-data",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )

    server._argos_service_token = settings.internal_service_token
    return server


def register_cattle_tools(server: Any, service: CattleQueryService) -> None:
    """Registra consultas SQL e Mongo detalhadas disponíveis ao agente Fazenda."""

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
    async def get_lot_report_source(payload: GetLotReportSourceRequest) -> dict[str, Any]:
        """Produz relatório completo de lote em envelope assinado e de curta validade."""
        result = await service.get_lot_report_source(get_access_context(), payload)
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

    @server.tool()
    async def list_cattle_documents(payload: ListCattleDocumentsRequest) -> dict[str, Any]:
        """Lista documentos Mongo de histórico bovino das propriedades autorizadas."""
        result = await service.list_cattle_documents(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def get_farm_summary(payload: FarmSummaryRequest) -> dict[str, Any]:
        """Soma lot.head_count dos lotes autorizados, sem adicionar exceções individuais.

        head_count_min/max filtram cada lote antes da soma. active é True por padrão;
        use active=null para agrupar ativos e inativos com group_by=active.
        """
        result = await service.get_farm_summary(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def summarize_exception_animals(
        payload: ExceptionSummaryRequest,
    ) -> dict[str, Any]:
        """Conta exceções autorizadas, com filtros de rebanho, lote, categoria e brinco.

        date_from/to referem-se à data da exceção, não à morte ou ao abate.
        A soma de pesos inclui somente pesos conhecidos, com contagem dos ausentes.
        """
        result = await service.summarize_exception_animals(get_access_context(), payload)
        return result.model_dump(mode="json")


def register_medicine_tools(server: Any, service: MedicineQueryService) -> None:
    """Registra consultas globais do catálogo destinadas ao agente Doutor."""

    @server.tool()
    async def list_medicines(payload: ListMedicinesRequest) -> dict[str, Any]:
        """Lista cadastro, indicação, dose e carência do catálogo SQL de medicamentos."""
        result = await service.list_medicines(get_access_context(), payload)
        return result.model_dump(mode="json")

    @server.tool()
    async def list_medicine_documents(
        payload: ListMedicineDocumentsRequest,
    ) -> dict[str, Any]:
        """Lista documentos Mongo de bula e detalhes de medicamentos."""
        result = await service.list_medicine_documents(get_access_context(), payload)
        return result.model_dump(mode="json")


def create_http_app(server: Any, delegation_verifier: DelegationTokenVerifier) -> Any:
    """Combina o app MCP do SDK com o middleware de delegação por requisição."""

    from packages.security.mcp_bearer import McpBearerMiddleware

    return McpBearerMiddleware(
        DelegationContextMiddleware(server.streamable_http_app(), delegation_verifier),
        server._argos_service_token,
    )
