"""Adapta os contratos Agent e AgentFactory para LangChain."""

import json
import unicodedata
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any
from uuid import UUID

from app.agent_schemas.results import (
    SourceReference,
    SpecialistAgentId,
    SpecialistResult,
    SpecialistStatus,
)
from app.agents.registry import AgentDefinition, AgentRegistry, ModelPolicyId
from app.application.operation_observation import OperationRecorder
from app.infrastructure.agents.execution_limits import ExecutionLimitCallback, ExecutionLimits
from app.infrastructure.agents.execution_observer import ExecutionObserver
from app.infrastructure.observability.langchain_callbacks import ObservabilityToolCallback
from app.infrastructure.observability.model_callbacks import ModelUsageCallback
from app.ports.agent import Agent, AgentId
from app.ports.execution_event_repository import ExecutionEventRepository
from pydantic import BaseModel, ValidationError

from packages.observability import (
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
)


class AgentFactoryConfigurationError(RuntimeError):
    """Configuração ausente ou incompatível com o Registry."""


class AgentResponseError(RuntimeError):
    """O agente não retornou uma saída compatível com seu schema."""


def _required_tool_evidence(agent_id: AgentId, limits: ExecutionLimits):
    evidence = limits.evidence_for(agent_id.value)
    if agent_id is AgentId.ANALYST:
        return tuple(item for item in evidence if item.tool_name == "estimate_medicine_consumption")
    return evidence


def _tool_evidence_answer(agent_id: AgentId, limits: ExecutionLimits) -> str:
    """Usa a saída válida da ferramenta quando o provider termina com mensagem vazia."""
    evidence = _required_tool_evidence(agent_id, limits)
    return "\n\n".join(
        f"Resultado verificado de {item.tool_name}: {item.excerpt}" for item in evidence
    )[: limits.max_evidence_chars]


def _specialist_evidence_fallback(
    agent_id: AgentId,
    limits: ExecutionLimits,
) -> SpecialistResult | None:
    evidence = _required_tool_evidence(agent_id, limits)
    if not evidence:
        return None
    complete_prediction = agent_id is AgentId.ANALYST and _has_complete_prediction_evidence(
        evidence
    )
    return SpecialistResult(
        agent=SpecialistAgentId(agent_id.value),
        status=SpecialistStatus.SUCCESS if complete_prediction else SpecialistStatus.PARTIAL,
        answer=(
            "A estimativa MCP foi concluída; quantidade, janela, cobertura e histórico "
            "estão na fonte verificada."
            if complete_prediction
            else _tool_evidence_answer(agent_id, limits)
        ),
        sources=tuple(
            SourceReference(
                title=f"Resultado verificado: {item.tool_name}",
                reference=item.reference,
                excerpt=item.excerpt,
            )
            for item in evidence
        ),
    )


def _has_complete_prediction_evidence(evidence: Sequence[Any]) -> bool:
    """Promove a estimativa determinística completa mesmo se o Analista se abstiver."""
    predictions = [item for item in evidence if item.tool_name == "estimate_medicine_consumption"]
    if not predictions:
        return False
    for item in predictions:
        try:
            payload = json.loads(item.excerpt)
        except TypeError, ValueError:
            return False
        if not isinstance(payload, Mapping):
            return False
        for key in ("structuredContent", "data", "content"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                payload = nested
                break
        items = payload.get("items")
        forecast = payload.get("forecast_window")
        coverage = payload.get("coverage")
        if (
            payload.get("status") == "estimated"
            and isinstance(forecast, Mapping)
            and forecast.get("start")
            and forecast.get("end")
            and isinstance(coverage, list)
            and coverage
            and all(
                isinstance(row, Mapping) and row.get("status") == "confirmed" for row in coverage
            )
            and isinstance(items, list)
            and items
            and all(
                isinstance(row, Mapping)
                and row.get("medicine_name")
                and row.get("unit")
                and row.get("estimated_quantity") is not None
                and row.get("estimated_application_events") is not None
                and row.get("estimated_head_administrations") is not None
                for row in items
            )
        ):
            continue
        return False
    return True


def _normalized_term(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character for character in decomposed if character.isalnum() or character.isspace()
    )


def _json_contains_value_for_key(value: Any, keys: frozenset[str]) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in keys and item not in (None, "", [], {}):
                return True
            if _json_contains_value_for_key(item, keys):
                return True
    elif isinstance(value, list):
        return any(_json_contains_value_for_key(item, keys) for item in value)
    return False


def _evidence_covers_missing_fields(missing_fields: Sequence[str], evidence: Sequence[Any]) -> bool:
    """Detecta quando o campo solicitado já aparece na saída da ferramenta."""
    if not missing_fields or not evidence:
        return False
    field_keys = {
        "principio ativo": frozenset({"active_ingredient"}),
        "ingrediente ativo": frozenset({"active_ingredient"}),
        "dose": frozenset({"dose"}),
        "carencia": frozenset({"meat_withdrawal_days"}),
        "codigo do lote": frozenset({"lot_code"}),
        "quantidade de bovinos": frozenset({"head_count"}),
        "propriedade": frozenset({"property_name"}),
    }
    decoded_evidence: list[tuple[str, Any]] = []
    for item in evidence:
        try:
            decoded = json.loads(item.excerpt)
        except TypeError, ValueError:
            continue
        decoded_evidence.append((item.tool_name, decoded))

    for missing_field in missing_fields:
        normalized_field = _normalized_term(missing_field)
        if any(term in normalized_field for term in ("ficha documental", "ficha tecnica", "bula")):
            if not any(
                tool_name == "list_medicine_documents"
                and _json_contains_value_for_key(decoded, frozenset({"items"}))
                for tool_name, decoded in decoded_evidence
            ):
                return False
            continue
        matching_keys = next(
            (
                keys
                for phrase, keys in field_keys.items()
                if _normalized_term(phrase) in normalized_field
            ),
            None,
        )
        if matching_keys is None or not any(
            _json_contains_value_for_key(decoded, matching_keys)
            for _tool_name, decoded in decoded_evidence
        ):
            return False
    return True


def validate_agent_configuration(
    registry: AgentRegistry,
    models: Mapping[ModelPolicyId, Any],
    agent_id: AgentId,
    prompt: str,
    tool_slots: Mapping[str, Sequence[Any]],
    output_schema: type[BaseModel],
) -> None:
    """Confere a configuração concreta usada no create_agent."""
    definition = registry.get(agent_id)

    if models.get(definition.model_policy) is None:
        raise AgentFactoryConfigurationError(
            f"Modelo não configurado para a política {definition.model_policy.value}"
        )

    if definition.prompt_key != agent_id.value:
        raise AgentFactoryConfigurationError(
            f"Referência de prompt incompatível para {agent_id.value}"
        )

    if not isinstance(prompt, str) or not prompt.strip():
        raise AgentFactoryConfigurationError(f"Prompt não configurado para {agent_id.value}")

    if set(tool_slots) != set(definition.tool_slots):
        raise AgentFactoryConfigurationError(
            f"Slots de ferramentas de {agent_id.value} não correspondem ao Registry"
        )

    tools = tuple(tool for slot in definition.tool_slots for tool in tool_slots[slot])
    actual_tool_names = tuple(getattr(tool, "name", None) for tool in tools)
    if any(not isinstance(name, str) or not name.strip() for name in actual_tool_names):
        raise AgentFactoryConfigurationError(
            f"Há ferramenta sem nome configurado para {agent_id.value}"
        )
    if len(set(actual_tool_names)) != len(actual_tool_names):
        raise AgentFactoryConfigurationError(
            f"Há nomes de ferramentas duplicados para {agent_id.value}"
        )

    if output_schema is not definition.output_schema:
        raise AgentFactoryConfigurationError(f"Schema de saída incompatível para {agent_id.value}")


class _LangChainAgent(Agent[Any, BaseModel]):
    def __init__(
        self,
        runnable: Any,
        definition: AgentDefinition,
        execution_events: ExecutionEventRepository | None = None,
        observation_collector: ObservationCollector | None = None,
    ) -> None:
        self._runnable = runnable
        self._input_schema = definition.input_schema
        self._output_schema = definition.output_schema
        self._agent_id = definition.agent_id
        self._execution_events = execution_events
        self._observation_collector = observation_collector

    async def run(
        self,
        input: Any,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> BaseModel:
        validated_input = self._input_schema.model_validate(input)
        observer = _observer_for_context(self._execution_events, context)
        invocation_id = None
        if observer is not None:
            invocation_id = await observer.agent_started(self._agent_id.value, validated_input)
        metric = (
            OperationRecorder(
                self._observation_collector,
                operation_kind=OperationKind.AGENT,
                operation_name=f"agent.{self._agent_id.value}",
                operation_phase=OperationPhase.EXECUTION,
                observation_level=ObservationLevel.SDK_INVOCATION,
                agent=self._agent_id.value,
            )
            if self._observation_collector is not None
            else None
        )
        config: dict[str, Any] | None = {"configurable": dict(context)} if context else None
        callbacks = []
        if observer is not None:
            callbacks.append(observer.tool_callback())
        if self._observation_collector is not None:
            callbacks.append(
                ObservabilityToolCallback(self._observation_collector, self._agent_id.value)
            )
        callbacks.append(
            ModelUsageCallback(self._observation_collector, agent=self._agent_id.value)
        )
        limits = (context or {}).get("argos_execution_limits")
        if isinstance(limits, ExecutionLimits):
            callbacks.append(ExecutionLimitCallback(limits, self._agent_id.value))
        if callbacks:
            config = config or {}
            config["callbacks"] = callbacks
        try:
            result = await self._runnable.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": validated_input.model_dump_json(),
                        }
                    ]
                },
                config=config,
            )
            if not isinstance(result, Mapping):
                raise AgentResponseError("LangChain retornou um estado inválido")
            try:
                raw_response = result.get("structured_response")
                if raw_response is None:
                    limits = (context or {}).get("argos_execution_limits")
                    if self._output_schema is SpecialistResult and isinstance(
                        limits, ExecutionLimits
                    ):
                        response = _specialist_evidence_fallback(self._agent_id, limits)
                        if response is None:
                            raise AgentResponseError(
                                "LangChain não retornou saída estruturada nem evidência suficiente"
                            )
                    else:
                        raise AgentResponseError("LangChain não retornou saída estruturada")
                else:
                    if (
                        self._output_schema is SpecialistResult
                        and isinstance(raw_response, Mapping)
                        and "agent" not in raw_response
                    ):
                        raw_response = {**raw_response, "agent": self._agent_id.value}
                    response = self._output_schema.model_validate(raw_response)
                    limits = (context or {}).get("argos_execution_limits")
                    if isinstance(response, SpecialistResult) and isinstance(
                        limits, ExecutionLimits
                    ):
                        all_tool_evidence = limits.evidence_for(self._agent_id.value)
                        can_use_evidence = response.status in {
                            SpecialistStatus.ABSTAIN,
                            SpecialistStatus.UNAVAILABLE,
                            SpecialistStatus.ERROR,
                        } or (
                            response.status
                            in {
                                SpecialistStatus.NEEDS_CLARIFICATION,
                                SpecialistStatus.NEEDS_INPUT,
                            }
                            and _evidence_covers_missing_fields(
                                response.missing_fields,
                                all_tool_evidence,
                            )
                        )
                        if can_use_evidence:
                            response = (
                                _specialist_evidence_fallback(self._agent_id, limits) or response
                            )
            except ValidationError as exc:
                raise AgentResponseError(
                    "Resposta do agente não corresponde ao schema registrado"
                ) from exc
        except BaseException as exc:
            for callback in callbacks:
                if isinstance(callback, ModelUsageCallback):
                    callback.finish_pending(exc)
            if metric is not None:
                metric.finish(error=exc)
            if observer is not None and invocation_id is not None:
                import asyncio

                await asyncio.shield(
                    observer.agent_finished(invocation_id, self._agent_id.value, failed=True)
                )
            raise
        if metric is not None:
            metric.finish()
        if observer is not None and invocation_id is not None:
            await observer.agent_finished(
                invocation_id,
                self._agent_id.value,
                output=response,
            )
        return response


def _observer_for_context(
    repository: ExecutionEventRepository | None,
    context: Mapping[str, Any] | None,
) -> ExecutionObserver | None:
    if repository is None or context is None:
        return None
    session_id = context.get("session_id")
    interaction_id = context.get("interaction_id")
    if not session_id or not interaction_id:
        return None
    return ExecutionObserver(
        repository,
        session_id=UUID(session_id),
        interaction_id=UUID(interaction_id),
    )


class LangChainAgentFactory:
    """Guarda os agentes criados e os entrega pelo ID registrado."""

    def __init__(
        self,
        registry: AgentRegistry,
        runnables: Mapping[AgentId, Any],
        execution_events: ExecutionEventRepository | None = None,
        observation_collector: ObservationCollector | None = None,
    ) -> None:
        self._registry = registry
        self._runnables = MappingProxyType(dict(runnables))
        self._execution_events = execution_events
        self._observation_collector = observation_collector

    def create(self, agent_id: AgentId) -> Agent[Any, Any]:
        definition = self._registry.get(agent_id)
        runnable = self._runnables.get(definition.agent_id)
        if runnable is None:
            raise AgentFactoryConfigurationError(
                f"Agente não configurado: {definition.agent_id.value}"
            )

        return _LangChainAgent(
            runnable,
            definition,
            self._execution_events,
            self._observation_collector,
        )
