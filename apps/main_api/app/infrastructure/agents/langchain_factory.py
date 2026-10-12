"""Adapta os contratos Agent e AgentFactory para LangChain."""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any
from uuid import UUID

from app.agents.registry import AgentDefinition, AgentRegistry, ModelPolicyId
from app.infrastructure.agents.execution_observer import ExecutionObserver
from app.infrastructure.observability.langchain_callbacks import ObservabilityToolCallback
from app.infrastructure.observability.model_callbacks import ModelUsageCallback
from app.infrastructure.observability.recording import OperationRecorder
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
                response = self._output_schema.model_validate(result.get("structured_response"))
            except ValidationError as exc:
                raise AgentResponseError(
                    "Resposta do agente não corresponde ao schema registrado"
                ) from exc
        except BaseException as exc:
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
