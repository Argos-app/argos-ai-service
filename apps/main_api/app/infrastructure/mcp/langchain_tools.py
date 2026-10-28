"""Wrappers LangChain locais para chamadas MCP autenticadas e limitadas por agente."""

from collections.abc import Callable

from langchain_core.tools import StructuredTool

from app.agents.tools.email import (
    build_email_prepare_tool,
    build_email_send_tool,
    build_email_status_tool,
)
from app.agents.tools.farm_reports import build_farm_report_tool
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.application.email_workflow import EmailWorkflow
from app.infrastructure.mcp.client import McpClients
from app.infrastructure.mcp.data_client import McpDataClient
from app.infrastructure.mcp.delegation import McpDelegationTokenIssuer
from app.infrastructure.mcp.tool_adapter import (
    McpToolBindingConfigurationError,
    _make_tool,
    _unavailable_tool,
)
from app.infrastructure.mcp.tool_adapter import (
    McpToolInput as McpToolInput,
)
from app.ports.agent import AgentId
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.report_artifact_repository import ReportArtifactRepository
from packages.observability import ObservationCollector

_FARM_TOOLS = {
    "list_properties": (
        "Lista propriedades autorizadas. Em payload, use name e active. property_ids é uma "
        "lista de IDs fornecidos pelo usuário ou retornados por esta consulta autorizada."
    ),
    "list_herds": (
        "Consulta rebanhos autorizados. Use os campos name, breed e active. property_ids é "
        "uma lista; omita se o usuário não informou IDs."
    ),
    "list_lots": (
        "Consulta lotes autorizados. Use lot_code (por exemplo, HC-LOTE-001) e property_ids "
        "como lista. Use IDs informados pelo usuário ou retornados por list_properties."
    ),
    "list_exception_animals": "Consulta animais em exceção nas propriedades autorizadas.",
    "list_applications": "Consulta aplicações de medicamentos nas propriedades autorizadas.",
    "list_application_animals": "Consulta detalhes por animal das aplicações autorizadas.",
    "list_occurrences": "Consulta ocorrências clínicas nas propriedades autorizadas.",
    "list_animal_lot_history": "Consulta o histórico de lotes de animais autorizados.",
    "list_cattle_documents": "Consulta documentos Mongo de histórico bovino autorizado.",
    "get_farm_summary": "Calcula resumo agregado do rebanho em propriedades autorizadas.",
    "summarize_exception_animals": "Calcula resumo de exceções nas propriedades autorizadas.",
}
_DOCTOR_TOOLS = {
    "list_medicines": (
        "Consulta o catálogo cadastral de medicamentos e suas indicações. Use search para "
        "buscar tanto por nome comercial quanto por princípio ativo; ou use brand_name e "
        "active_ingredient para filtros separados."
    ),
    "list_medicine_documents": (
        "Consulta documentos de bula e detalhes de medicamentos. Use search para buscar tanto "
        "por nome comercial quanto por princípio ativo; ou use filtros separados."
    ),
}
_ANALYST_TOOLS = {
    "list_properties": (
        "Resolve o nome de uma propriedade para um ID autorizado. Use name com o nome citado "
        "pelo usuário e confira se houve exatamente um resultado antes da predição."
    ),
    "estimate_medicine_consumption": (
        "Estima consumo com base no histórico coberto das propriedades autorizadas. O payload "
        "aceita property_ids (lista, IDs informados pelo usuário ou confirmados por "
        "list_properties), medicine_names "
        "(lista), as_of (data YYYY-MM-DD já encerrada) e horizon_days (1 a 28; padrão 28). "
        "Omitir as_of usa ontem. Esta ferramenta não consulta estoque."
    ),
}
_CATTLE_OPERATIONS = frozenset(
    {
        "list_properties",
        "list_herds",
        "list_lots",
        "list_exception_animals",
        "list_applications",
        "list_application_animals",
        "list_occurrences",
        "list_animal_lot_history",
        "list_cattle_documents",
        "get_farm_summary",
        "summarize_exception_animals",
    }
)


def build_mcp_tool_bindings(
    clients: McpClients,
    delegation_issuer: McpDelegationTokenIssuer | None,
    report_artifacts: ReportArtifactRepository | None = None,
    email_preparations: EmailPreparationRepository | None = None,
    observation_collector: ObservationCollector | None = None,
    report_signature_checker: Callable[[bytes, str, str], bool] | None = None,
) -> dict[AgentId, dict[str, tuple[StructuredTool, ...]]]:
    """Cria toolsets estáticos por agente; o modelo nunca escolhe agente/operação.

    Receber McpClients indica configuração explícita de produção.
    Serviços ausentes geram ferramentas indisponíveis, sem fallback silencioso para mocks."""
    if (clients.data is not None or clients.prediction is not None) and delegation_issuer is None:
        raise McpToolBindingConfigurationError(
            "MAIN_API_MCP_DELEGATION_* é obrigatório para os MCPs de dados e predição"
        )

    bindings: dict[AgentId, dict[str, tuple[StructuredTool, ...]]] = {}

    mcp_configured = True
    if clients.data is not None:
        farm_tools = tuple(
            _make_tool(
                clients.data,
                name=name,
                description=description,
                issuer=delegation_issuer,
                agent="fazenda",
                operation=("cattle.read" if name in _CATTLE_OPERATIONS else "medicine.read"),
            )
            for name, description in _FARM_TOOLS.items()
            if name in clients.data.allowed_tools
        )
        if "list_lots" in clients.data.allowed_tools:
            farm_tools = (
                *farm_tools,
                build_farm_report_tool(
                    McpDataClient(clients.data, delegation_issuer),
                    artifact_repository=report_artifacts,
                    signature_checker=report_signature_checker,
                ),
            )
        if farm_tools:
            bindings[AgentId.FARM] = {"farm_data": farm_tools}
        doctor_tools = tuple(
            _make_tool(
                clients.data,
                name=name,
                description=description,
                issuer=delegation_issuer,
                agent="doutor",
                operation="medicine.read",
            )
            for name, description in _DOCTOR_TOOLS.items()
            if name in clients.data.allowed_tools
        )
        if doctor_tools:
            bindings[AgentId.DOCTOR] = {"medicine_reference": doctor_tools}
    analyst_tools: list[StructuredTool] = []
    if clients.data is not None and "list_properties" in clients.data.allowed_tools:
        analyst_tools.append(
            _make_tool(
                clients.data,
                name="list_properties",
                description=_ANALYST_TOOLS["list_properties"],
                issuer=delegation_issuer,
                agent="analista",
                operation="cattle.read",
            )
        )
    if (
        clients.prediction is not None
        and "estimate_medicine_consumption" in clients.prediction.allowed_tools
    ):
        analyst_tools.append(
            _make_tool(
                clients.prediction,
                name="estimate_medicine_consumption",
                description=_ANALYST_TOOLS["estimate_medicine_consumption"],
                issuer=delegation_issuer,
                agent="analista",
                operation="prediction.read",
            )
        )
    if analyst_tools:
        bindings[AgentId.ANALYST] = {"prediction": tuple(analyst_tools)}
    if clients.email is not None:
        email_tools: tuple[StructuredTool, ...] = ()
        workflow = None
        dispatch_workflow = None
        if report_artifacts is not None and email_preparations is not None:
            workflow = EmailWorkflow(
                email_client=clients.email,
                report_artifacts=report_artifacts,
                preparations=email_preparations,
                observation_collector=observation_collector,
            )
            dispatch_workflow = EmailDispatchWorkflow(
                email_client=clients.email,
                preparations=email_preparations,
                report_artifacts=report_artifacts,
                observation_collector=observation_collector,
            )
        if "prepare_report" in clients.email.allowed_tools:
            email_tools = (*email_tools, build_email_prepare_tool(workflow))
        if "send_report" in clients.email.allowed_tools:
            email_tools = (*email_tools, build_email_send_tool(dispatch_workflow))
        if "get_report_status" in clients.email.allowed_tools:
            email_tools = (*email_tools, build_email_status_tool(dispatch_workflow))
        if email_tools:
            bindings[AgentId.SECRETARY] = {"report_delivery": email_tools}
    if mcp_configured:
        bindings.setdefault(AgentId.FARM, {}).setdefault(
            "farm_data",
            (
                _unavailable_tool(
                    "mcp_data_unavailable",
                    "Consulta dados autorizados somente quando o MCP de dados está configurado.",
                    "MCP de dados não configurado; nenhuma fonte de negócio foi consultada.",
                ),
            ),
        )
        bindings.setdefault(AgentId.DOCTOR, {}).setdefault(
            "medicine_reference",
            (
                _unavailable_tool(
                    "mcp_medicine_unavailable",
                    "Consulta medicamentos somente quando o MCP de dados está configurado.",
                    "MCP de dados não configurado; nenhum catálogo ou documento foi consultado.",
                ),
            ),
        )
        bindings.setdefault(AgentId.ANALYST, {}).setdefault(
            "prediction",
            (
                _unavailable_tool(
                    "mcp_prediction_unavailable",
                    "Executa predições somente quando o MCP de predição está configurado.",
                    "MCP de predição não configurado; nenhum valor foi calculado.",
                ),
            ),
        )
        bindings.setdefault(AgentId.SECRETARY, {}).setdefault(
            "report_delivery",
            (
                _unavailable_tool(
                    "mcp_email_unavailable",
                    "Prepara ou consulta relatórios somente quando o MCP de e-mail "
                    "está configurado.",
                    "MCP de e-mail não configurado; nenhuma operação foi executada.",
                ),
            ),
        )
        bindings.setdefault(AgentId.INFORMATION, {}).setdefault(
            "documental_a2a",
            (
                _unavailable_tool(
                    "documental_unavailable",
                    "Consulta Documental somente quando o serviço estiver configurado.",
                    "Serviço Documental não configurado; nenhum documento foi consultado.",
                ),
            ),
        )
    return bindings
