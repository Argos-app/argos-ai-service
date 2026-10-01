"""Adapta os contratos Agent e AgentFactory para LangChain."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from app.agents.registry import AgentDefinition, AgentRegistry, ModelPolicyId
from app.ports.agent import Agent, AgentId
from langchain.agents import create_agent
from pydantic import BaseModel, ValidationError


class AgentFactoryConfigurationError(RuntimeError):
    """Dependência necessária para montar um agente ausente ou inconsistente."""


class AgentResponseError(RuntimeError):
    """O agente não retornou uma saída compatível com o schema registrado."""


class _LangChainAgent(Agent[Any, BaseModel]):
    def __init__(self, runnable: Any, definition: AgentDefinition) -> None:
        self._runnable = runnable
        self._input_schema = definition.input_schema
        self._output_schema = definition.output_schema

    async def run(self, input: Any) -> BaseModel:
        validated_input = self._input_schema.model_validate(input)
        result = await self._runnable.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": validated_input.model_dump_json(),
                    }
                ]
            }
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
    """Resolve definições do Registry e monta agentes sem estado de sessão."""

    def __init__(
        self,
        registry: AgentRegistry,
        models: Mapping[ModelPolicyId, Any],
        prompts: Mapping[tuple[str, str], str],
        tools: Mapping[str, Any],
    ) -> None:
        self._registry = registry
        self._models = MappingProxyType(dict(models))
        self._prompts = MappingProxyType(dict(prompts))
        self._tools = MappingProxyType(dict(tools))

    def create(self, agent_id: AgentId) -> Agent[Any, Any]:
        definition = self._registry.get(agent_id)
        model = self._models.get(definition.model_policy)
        if model is None:
            raise AgentFactoryConfigurationError(
                f"Modelo não configurado para a política {definition.model_policy.value}"
            )

        prompt_ref = (definition.prompt_key, definition.prompt_version)
        prompt = self._prompts.get(prompt_ref)
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentFactoryConfigurationError(
                f"Prompt não configurado: {definition.prompt_key}@{definition.prompt_version}"
            )

        missing_tools = set(definition.tool_names) - self._tools.keys()
        if missing_tools:
            names = ", ".join(sorted(missing_tools))
            raise AgentFactoryConfigurationError(f"Ferramentas não configuradas: {names}")

        tools = [self._tools[name] for name in definition.tool_names]
        for name, tool in zip(definition.tool_names, tools, strict=True):
            actual_name = getattr(tool, "name", None)
            if actual_name != name:
                raise AgentFactoryConfigurationError(
                    f"Ferramenta registrada como {name!r}, mas implementação declara "
                    f"{actual_name!r}"
                )

        try:
            runnable = create_agent(
                model=model,
                tools=tools,
                system_prompt=prompt,
                response_format=definition.output_schema,
            )
        except Exception as exc:
            raise AgentFactoryConfigurationError(
                f"Não foi possível montar o agente {definition.agent_id.value}"
            ) from exc

        return _LangChainAgent(runnable, definition)
