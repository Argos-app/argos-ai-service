from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.agent_schemas.inputs import (
    QuestionFormatterInput,
    RouterInput,
    SpecialistInput,
)
from app.agent_schemas.results import (
    JudgeResult,
    QuestionFormatterResult,
    RoutePlan,
    SpecialistResult,
)
from app.agent_schemas.synthesis import (
    JudgeInput,
    OrchestratorInput,
    OrchestratorResult,
)
from app.ports.agent import AgentId
from pydantic import BaseModel


class ModelPolicyId(StrEnum):
    """Referências de política de modelo, sem nomes de provedores ou modelos."""

    ROUTER = "router"
    FARM = "farm"
    DOCTOR = "doctor"
    ANALYST = "analyst"
    SECRETARY = "secretary"
    INFORMATION = "information"
    ORCHESTRATOR = "orchestrator"
    JUDGE = "judge"
    QUESTION_FORMATTER = "question_formatter"


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Configuração declarativa necessária para identificar e montar um agente."""

    agent_id: AgentId
    prompt_key: str
    prompt_version: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    tool_slots: tuple[str, ...]
    model_policy: ModelPolicyId


class AgentNotRegisteredError(LookupError):
    """O ID não está registrado ou ainda não tem contrato de agente completo."""


_EXPECTED_SCHEMAS: dict[AgentId, tuple[type[BaseModel], type[BaseModel]]] = {
    AgentId.ROUTER: (RouterInput, RoutePlan),
    AgentId.FARM: (SpecialistInput, SpecialistResult),
    AgentId.DOCTOR: (SpecialistInput, SpecialistResult),
    AgentId.ANALYST: (SpecialistInput, SpecialistResult),
    AgentId.SECRETARY: (SpecialistInput, SpecialistResult),
    AgentId.INFORMATION: (SpecialistInput, SpecialistResult),
    AgentId.ORCHESTRATOR: (OrchestratorInput, OrchestratorResult),
    AgentId.JUDGE: (JudgeInput, JudgeResult),
    AgentId.QUESTION_FORMATTER: (QuestionFormatterInput, QuestionFormatterResult),
}

ALLOWED_TOOL_SLOTS: dict[AgentId, frozenset[str]] = {
    AgentId.ROUTER: frozenset(),
    AgentId.FARM: frozenset({"farm_data"}),
    AgentId.DOCTOR: frozenset({"medicine_reference"}),
    AgentId.ANALYST: frozenset({"prediction"}),
    AgentId.SECRETARY: frozenset({"report_delivery"}),
    AgentId.INFORMATION: frozenset({"documental_a2a"}),
    AgentId.ORCHESTRATOR: frozenset(),
    AgentId.JUDGE: frozenset(),
    AgentId.QUESTION_FORMATTER: frozenset(),
}

ALLOWED_TOOL_NAMES_BY_SLOT: dict[tuple[AgentId, str], frozenset[str]] = {
    (AgentId.INFORMATION, "documental_a2a"): frozenset(
        {
            "consultar_documental",
            "consultar_legal",
            "mock_consultar_documental",
            "mock_consultar_legal",
            "documental_unavailable",
        }
    ),
    (AgentId.SECRETARY, "report_delivery"): frozenset(
        {
            "prepare_report",
            "send_report",
            "get_report_status",
            "mcp_email_unavailable",
            "mock_preparar_relatorio",
        }
    ),
}

_EXPECTED_MODEL_POLICIES: dict[AgentId, ModelPolicyId] = {
    AgentId.ROUTER: ModelPolicyId.ROUTER,
    AgentId.FARM: ModelPolicyId.FARM,
    AgentId.DOCTOR: ModelPolicyId.DOCTOR,
    AgentId.ANALYST: ModelPolicyId.ANALYST,
    AgentId.SECRETARY: ModelPolicyId.SECRETARY,
    AgentId.INFORMATION: ModelPolicyId.INFORMATION,
    AgentId.ORCHESTRATOR: ModelPolicyId.ORCHESTRATOR,
    AgentId.JUDGE: ModelPolicyId.JUDGE,
    AgentId.QUESTION_FORMATTER: ModelPolicyId.QUESTION_FORMATTER,
}


class AgentRegistry:
    """Catálogo imutável; não guarda instâncias nem estado de execução."""

    def __init__(self, definitions: Iterable[AgentDefinition]) -> None:
        by_id: dict[AgentId, AgentDefinition] = {}
        for definition in definitions:
            self._validate(definition)
            if definition.agent_id in by_id:
                raise ValueError(f"AgentId duplicado: {definition.agent_id.value}")
            by_id[definition.agent_id] = definition

        self._definitions: Mapping[AgentId, AgentDefinition] = MappingProxyType(by_id)

    @staticmethod
    def _validate(definition: AgentDefinition) -> None:
        if not isinstance(definition.agent_id, AgentId):
            raise TypeError("agent_id deve ser um AgentId conhecido")

        expected_schemas = _EXPECTED_SCHEMAS.get(definition.agent_id)
        if expected_schemas is None:
            raise AgentNotRegisteredError(
                f"Agente sem contrato de schemas registrado: {definition.agent_id.value}"
            )

        for field_name in ("prompt_key", "prompt_version"):
            value = getattr(definition, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} não pode ser vazio")

        if not isinstance(definition.model_policy, ModelPolicyId):
            raise TypeError("model_policy deve ser uma política registrada")

        for field_name in ("input_schema", "output_schema"):
            schema = getattr(definition, field_name)
            if not isinstance(schema, type) or not issubclass(schema, BaseModel):
                raise TypeError(f"{field_name} deve ser uma classe Pydantic")

        if (definition.input_schema, definition.output_schema) != expected_schemas:
            raise TypeError(f"Schemas incompatíveis para {definition.agent_id.value}")

        if not isinstance(definition.tool_slots, tuple):
            raise TypeError("tool_slots deve ser uma tupla imutável")
        if any(not isinstance(slot, str) or not slot.strip() for slot in definition.tool_slots):
            raise ValueError("tool_slots não pode conter identificadores vazios")
        if len(set(definition.tool_slots)) != len(definition.tool_slots):
            raise ValueError(f"Slot de ferramenta duplicado para {definition.agent_id.value}")

        allowed_slots = ALLOWED_TOOL_SLOTS[definition.agent_id]
        if not set(definition.tool_slots) <= allowed_slots:
            raise ValueError(f"Slot de ferramenta não permitido para {definition.agent_id.value}")

        expected_policy = _EXPECTED_MODEL_POLICIES[definition.agent_id]
        if definition.model_policy is not expected_policy:
            raise ValueError(f"Política de modelo incompatível para {definition.agent_id.value}")

    def get(self, agent_id: AgentId | str) -> AgentDefinition:
        """Retorna a definição ou falha de forma controlada para IDs ausentes."""
        try:
            normalized_id = AgentId(agent_id)
        except (TypeError, ValueError) as exc:
            raise AgentNotRegisteredError(f"AgentId desconhecido: {agent_id!r}") from exc

        try:
            return self._definitions[normalized_id]
        except KeyError as exc:
            raise AgentNotRegisteredError(
                f"Agente sem definição registrada: {normalized_id.value}"
            ) from exc

    @property
    def agent_ids(self) -> frozenset[AgentId]:
        """IDs cadastrados no catálogo, sem expor seu armazenamento interno."""
        return frozenset(self._definitions)


DEFAULT_AGENT_REGISTRY = AgentRegistry(
    (
        AgentDefinition(
            agent_id=AgentId.ROUTER,
            prompt_key="router",
            prompt_version="2",
            input_schema=RouterInput,
            output_schema=RoutePlan,
            tool_slots=(),
            model_policy=ModelPolicyId.ROUTER,
        ),
        AgentDefinition(
            agent_id=AgentId.FARM,
            prompt_key="farm",
            prompt_version="2",
            input_schema=SpecialistInput,
            output_schema=SpecialistResult,
            tool_slots=("farm_data",),
            model_policy=ModelPolicyId.FARM,
        ),
        AgentDefinition(
            agent_id=AgentId.DOCTOR,
            prompt_key="doctor",
            prompt_version="1",
            input_schema=SpecialistInput,
            output_schema=SpecialistResult,
            tool_slots=("medicine_reference",),
            model_policy=ModelPolicyId.DOCTOR,
        ),
        AgentDefinition(
            agent_id=AgentId.ANALYST,
            prompt_key="analyst",
            prompt_version="1",
            input_schema=SpecialistInput,
            output_schema=SpecialistResult,
            tool_slots=("prediction",),
            model_policy=ModelPolicyId.ANALYST,
        ),
        AgentDefinition(
            agent_id=AgentId.SECRETARY,
            prompt_key="secretary",
            prompt_version="2",
            input_schema=SpecialistInput,
            output_schema=SpecialistResult,
            tool_slots=("report_delivery",),
            model_policy=ModelPolicyId.SECRETARY,
        ),
        AgentDefinition(
            agent_id=AgentId.INFORMATION,
            prompt_key="information",
            prompt_version="1",
            input_schema=SpecialistInput,
            output_schema=SpecialistResult,
            tool_slots=("documental_a2a",),
            model_policy=ModelPolicyId.INFORMATION,
        ),
        AgentDefinition(
            agent_id=AgentId.ORCHESTRATOR,
            prompt_key="orchestrator",
            prompt_version="2",
            input_schema=OrchestratorInput,
            output_schema=OrchestratorResult,
            tool_slots=(),
            model_policy=ModelPolicyId.ORCHESTRATOR,
        ),
        AgentDefinition(
            agent_id=AgentId.JUDGE,
            prompt_key="judge",
            prompt_version="2",
            input_schema=JudgeInput,
            output_schema=JudgeResult,
            tool_slots=(),
            model_policy=ModelPolicyId.JUDGE,
        ),
        AgentDefinition(
            agent_id=AgentId.QUESTION_FORMATTER,
            prompt_key="question_formatter",
            prompt_version="1",
            input_schema=QuestionFormatterInput,
            output_schema=QuestionFormatterResult,
            tool_slots=(),
            model_policy=ModelPolicyId.QUESTION_FORMATTER,
        ),
    )
)
