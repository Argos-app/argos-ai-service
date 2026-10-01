"""Adapta os contratos Agent e AgentFactory para LangChain."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from app.agents.registry import AgentDefinition, AgentRegistry, ModelPolicyId
from app.ports.agent import Agent, AgentId
from pydantic import BaseModel, ValidationError


class AgentFactoryConfigurationError(RuntimeError):
    """Configuração ausente ou incompatível com o Registry."""


class AgentResponseError(RuntimeError):
    """O agente não retornou uma saída compatível com seu schema."""


def validate_agent_configuration(
    registry: AgentRegistry,
    models: Mapping[ModelPolicyId, Any],
    agent_id: AgentId,
    prompt: str,
    tools: list[Any],
    output_schema: type[BaseModel],
) -> None:
    """Confere a configuração concreta usada no create_agent."""
    definition = registry.get(agent_id)

    if models.get(definition.model_policy) is None:
        raise AgentFactoryConfigurationError(
            f"Modelo não configurado para a política {definition.model_policy.value}"
        )

    if (definition.prompt_key, definition.prompt_version) != (agent_id.value, "1"):
        raise AgentFactoryConfigurationError(
            f"Referência de prompt incompatível para {agent_id.value}"
        )

    if not isinstance(prompt, str) or not prompt.strip():
        raise AgentFactoryConfigurationError(f"Prompt não configurado para {agent_id.value}")

    actual_tool_names = tuple(getattr(tool, "name", None) for tool in tools)
    if actual_tool_names != definition.tool_names:
        raise AgentFactoryConfigurationError(
            f"Ferramentas de {agent_id.value} não correspondem ao Registry"
        )

    if output_schema is not definition.output_schema:
        raise AgentFactoryConfigurationError(f"Schema de saída incompatível para {agent_id.value}")


class _LangChainAgent(Agent[Any, BaseModel]):
    def __init__(self, runnable: Any, definition: AgentDefinition) -> None:
        self._runnable = runnable
        self._input_schema = definition.input_schema
        self._output_schema = definition.output_schema

    async def run(
        self,
        input: Any,
        *,
        context: Mapping[str, str] | None = None,
    ) -> BaseModel:
        validated_input = self._input_schema.model_validate(input)
        config = {"configurable": dict(context)} if context is not None else None

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
            return self._output_schema.model_validate(result.get("structured_response"))
        except ValidationError as exc:
            raise AgentResponseError(
                "Resposta do agente não corresponde ao schema registrado"
            ) from exc


class LangChainAgentFactory:
    """Guarda os agentes criados e os entrega pelo ID registrado."""

    def __init__(
        self,
        registry: AgentRegistry,
        runnables: Mapping[AgentId, Any],
    ) -> None:
        self._registry = registry
        self._runnables = MappingProxyType(dict(runnables))

    def create(self, agent_id: AgentId) -> Agent[Any, Any]:
        definition = self._registry.get(agent_id)
        runnable = self._runnables.get(definition.agent_id)
        if runnable is None:
            raise AgentFactoryConfigurationError(
                f"Agente não configurado: {definition.agent_id.value}"
            )

        return _LangChainAgent(runnable, definition)
