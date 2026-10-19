"""Wrappers LangChain locais para chamadas MCP autenticadas e limitadas por agente."""

import json
from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from app.agents.tools.email import (
    build_email_prepare_tool,
    build_email_send_tool,
    build_email_status_tool,
)
from app.agents.tools.farm_reports import build_farm_report_tool
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.application.email_workflow import EmailWorkflow
from app.infrastructure.mcp.client import McpClients, McpToolServerClient
from app.infrastructure.mcp.data_client import McpDataClient
from app.infrastructure.mcp.delegation import McpDelegationTokenIssuer
from app.infrastructure.mcp.tool_schemas import MCP_TOOL_PAYLOAD_SCHEMAS
from app.ports.agent import AgentId
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.report_artifact_repository import ReportArtifactRepository
from packages.observability import ObservationCollector


class McpToolBindingConfigurationError(ValueError):
    """Clientes MCP não podem ser expostos sem o emissor de delegações exigido."""


class McpToolInput(BaseModel):
    """Formato de transporte dos servidores Argos: um objeto `payload` por tool."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


_PAYLOAD_ALIASES: dict[str, dict[str, str]] = {
    "list_lots": {"code": "lot_code"},
    "list_applications": {"medication_name": "medicine_name"},
    "estimate_medicine_consumption": {
        "property_id": "property_ids",
        "medicine_name": "medicine_names",
    },
}


def _normalize_model_payload(name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map common LLM argument variants to the canonical MCP contract."""
    normalized = dict(payload)
    for alias, canonical in _PAYLOAD_ALIASES.get(name, {}).items():
        if alias not in normalized or normalized[alias] is None:
            continue
        value = normalized.pop(alias)
        if canonical.endswith("_ids") or canonical.endswith("_names"):
            value = value if isinstance(value, (list, tuple)) else [value]
        existing = normalized.get(canonical)
        if existing is None:
            normalized[canonical] = value
            continue
        if canonical.endswith("_ids") or canonical.endswith("_names"):
            existing = list(existing)
        if existing != value:
            raise ValueError(f"argumentos {alias} e {canonical} conflitantes")

    if normalized.get("property_id") is not None:
        value = [normalized.pop("property_id")]
        existing = normalized.get("property_ids")
        if existing is not None and list(existing) != value:
            raise ValueError("argumentos property_id e property_ids conflitantes")
        normalized["property_ids"] = value
    return normalized


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
        "Consulta lotes autorizados. Prefira lot_code (por exemplo, HC-LOTE-001); code também "
        "é aceito e normalizado. property_ids é uma lista; property_id também é aceito para "
        "um único ID. Use IDs informados pelo usuário ou retornados por list_properties."
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


def _trusted_config(config: RunnableConfig | None) -> Mapping[str, Any]:
    configurable = (config or {}).get("configurable") or {}
    if not isinstance(configurable, Mapping):
        raise ValueError("contexto autenticado da chamada MCP está ausente")
    return configurable


def _mcp_result_text(result: Any) -> str:
    """Retorna conteúdo útil ao agente sem expor envelope interno do transporte."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, default=str)
    content = getattr(result, "content", ())
    text_blocks = [text for block in content if (text := getattr(block, "text", None))]
    if text_blocks:
        return "\n".join(text_blocks)
    return "O MCP retornou uma resposta vazia."


def _delegation_headers(
    *,
    service: str,
    agent: str,
    operation: str,
    config: RunnableConfig | None,
    issuer: McpDelegationTokenIssuer | None,
) -> dict[str, str]:
    if issuer is None:
        raise McpToolBindingConfigurationError(f"emissor de delegação ausente para o MCP {service}")
    context = _trusted_config(config)
    user_id = context.get("argos_user_id")
    property_ids = context.get("argos_authorized_property_ids")
    request_id = context.get("argos_request_id")
    if not user_id or not property_ids or not request_id:
        raise ValueError("identidade e propriedades autorizadas são obrigatórias para a tool MCP")
    return issuer.headers_for_call(
        service=service,
        user_id=user_id,
        authorized_property_ids=property_ids,
        agent=agent,
        operation=operation,
        request_id=request_id,
    )


def _make_tool(
    client: McpToolServerClient,
    *,
    name: str,
    description: str,
    issuer: McpDelegationTokenIssuer | None,
    agent: str | None = None,
    operation: str | None = None,
) -> StructuredTool:
    payload_schema = MCP_TOOL_PAYLOAD_SCHEMAS.get(name)
    args_schema = create_model(
        f"{name.title().replace('_', '')}Input",
        __base__=McpToolInput,
        payload=(payload_schema or dict[str, Any], Field(description=description)),
    )

    async def invoke(
        payload: dict[str, Any] | BaseModel,
        config: RunnableConfig,
    ) -> str:
        if payload_schema is None:
            request_payload = dict(payload)
        else:
            raw_payload = (
                payload.model_dump(mode="python") if isinstance(payload, BaseModel) else payload
            )
            normalized_payload = _normalize_model_payload(name, raw_payload)
            request_payload = payload_schema.model_validate(normalized_payload).model_dump(
                mode="json", exclude_none=True
            )
        request_headers: dict[str, str] | None = None
        if agent is not None and operation is not None:
            request_headers = _delegation_headers(
                service=client.service_name,
                agent=agent,
                operation=operation,
                config=config,
                issuer=issuer,
            )
        result = await client.call_tool(
            name,
            {"payload": request_payload},
            request_headers=request_headers,
        )
        return _mcp_result_text(result)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=description,
        args_schema=args_schema,
    )


def _unavailable_tool(name: str, description: str, message: str) -> StructuredTool:
    def unavailable() -> str:
        return message

    return StructuredTool.from_function(
        func=unavailable,
        name=name,
        description=description,
    )


def build_mcp_tool_bindings(
    clients: McpClients,
    delegation_issuer: McpDelegationTokenIssuer | None,
    report_artifacts: ReportArtifactRepository | None = None,
    email_preparations: EmailPreparationRepository | None = None,
    observation_collector: ObservationCollector | None = None,
    report_signature_checker: Callable[[bytes, str, str], bool] | None = None,
) -> dict[AgentId, dict[str, tuple[StructuredTool, ...]]]:
    """Cria toolsets estáticos por agente; o modelo nunca escolhe agente/operação."""
    if (clients.data is not None or clients.prediction is not None) and delegation_issuer is None:
        raise McpToolBindingConfigurationError(
            "MAIN_API_MCP_DELEGATION_* é obrigatório para os MCPs de dados e predição"
        )

    bindings: dict[AgentId, dict[str, tuple[StructuredTool, ...]]] = {}
    # Receiving an McpClients object is an explicit production configuration.
    # Missing services get unavailable tools rather than silently falling back to mocks.
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
