from collections.abc import Mapping, Sequence
from typing import Any

from app.agent_schemas.results import (
    JudgeResult,
    QuestionFormatterResult,
    RoutePlan,
    SpecialistResult,
)
from app.agent_schemas.synthesis import OrchestratorResult
from app.agents.mock_tools import MOCK_AGENT_TOOL_BINDINGS
from app.agents.registry import DEFAULT_AGENT_REGISTRY, AgentRegistry, ModelPolicyId
from app.infrastructure.agents.langchain_factory import (
    AgentFactoryConfigurationError,
    LangChainAgentFactory,
    validate_agent_configuration,
)
from app.infrastructure.mcp.client import McpClients
from app.infrastructure.mcp.delegation import McpDelegationTokenIssuer
from app.infrastructure.mcp.langchain_tools import build_mcp_tool_bindings
from app.ports.agent import AgentId
from app.ports.execution_event_repository import ExecutionEventRepository
from app.prompts.judge import JUDGE_SYSTEM_PROMPT
from app.prompts.orchestrator import ORCHESTRATOR_SYSTEM_PROMPT
from app.prompts.question_formatter import QUESTION_FORMATTER_SYSTEM_PROMPT
from app.prompts.router import ROUTER_SYSTEM_PROMPT
from app.prompts.specialists import (
    ANALYST_SYSTEM_PROMPT,
    DOCTOR_SYSTEM_PROMPT,
    FARM_SYSTEM_PROMPT,
    INFORMATION_SYSTEM_PROMPT,
    SECRETARY_SYSTEM_PROMPT,
)
from langchain.agents import create_agent
from langchain_core.tools import BaseTool

AgentToolBindings = Mapping[AgentId, Mapping[str, Sequence[BaseTool]]]


def resolve_tool_bindings(
    registry: AgentRegistry,
    overrides: AgentToolBindings | None,
) -> dict[AgentId, dict[str, tuple[BaseTool, ...]]]:
    resolved = {
        agent_id: {slot: tuple(tools) for slot, tools in slots.items()}
        for agent_id, slots in MOCK_AGENT_TOOL_BINDINGS.items()
    }
    for agent_id in AgentId:
        resolved.setdefault(agent_id, {})

    for raw_agent_id, slots in (overrides or {}).items():
        try:
            agent_id = AgentId(raw_agent_id)
        except (TypeError, ValueError) as exc:
            raise AgentFactoryConfigurationError(
                f"Agente sem slots de ferramentas conhecidos: {raw_agent_id!r}"
            ) from exc

        definition = registry.get(agent_id)
        unknown_slots = set(slots) - set(definition.tool_slots)
        if unknown_slots:
            raise AgentFactoryConfigurationError(
                f"Slots não permitidos para {agent_id.value}: {sorted(unknown_slots)}"
            )

        current = resolved.setdefault(agent_id, {})
        current.update({slot: tuple(tools) for slot, tools in slots.items()})

    for agent_id in AgentId:
        definition = registry.get(agent_id)
        slots = resolved.get(agent_id, {})
        if set(slots) != set(definition.tool_slots):
            raise AgentFactoryConfigurationError(
                f"Slots de ferramentas incompletos para {agent_id.value}"
            )
        if any(not tools for tools in slots.values()):
            raise AgentFactoryConfigurationError(
                f"Slot de ferramenta sem implementação para {agent_id.value}"
            )

    return resolved


def build_agent_factory(
    models: Mapping[ModelPolicyId, Any],
    registry: AgentRegistry = DEFAULT_AGENT_REGISTRY,
    tool_bindings: AgentToolBindings | None = None,
    execution_events: ExecutionEventRepository | None = None,
    *,
    mcp_clients: McpClients | None = None,
    delegation_issuer: McpDelegationTokenIssuer | None = None,
) -> LangChainAgentFactory:
    overrides: dict[AgentId, dict[str, tuple[BaseTool, ...]]] = {}
    if mcp_clients is not None:
        overrides.update(build_mcp_tool_bindings(mcp_clients, delegation_issuer))
    for agent_id, slots in (tool_bindings or {}).items():
        overrides.setdefault(agent_id, {}).update(
            {slot: tuple(tools) for slot, tools in slots.items()}
        )
    resolved_tool_bindings = resolve_tool_bindings(registry, overrides)

    def tools_for(agent_id: AgentId) -> list[BaseTool]:
        definition = registry.get(agent_id)
        return [
            tool
            for slot in definition.tool_slots
            for tool in resolved_tool_bindings[agent_id][slot]
        ]

    configurations = (
        (AgentId.ROUTER, ROUTER_SYSTEM_PROMPT, RoutePlan),
        (AgentId.FARM, FARM_SYSTEM_PROMPT, SpecialistResult),
        (AgentId.DOCTOR, DOCTOR_SYSTEM_PROMPT, SpecialistResult),
        (AgentId.ANALYST, ANALYST_SYSTEM_PROMPT, SpecialistResult),
        (AgentId.SECRETARY, SECRETARY_SYSTEM_PROMPT, SpecialistResult),
        (AgentId.INFORMATION, INFORMATION_SYSTEM_PROMPT, SpecialistResult),
        (AgentId.ORCHESTRATOR, ORCHESTRATOR_SYSTEM_PROMPT, OrchestratorResult),
        (AgentId.JUDGE, JUDGE_SYSTEM_PROMPT, JudgeResult),
        (
            AgentId.QUESTION_FORMATTER,
            QUESTION_FORMATTER_SYSTEM_PROMPT,
            QuestionFormatterResult,
        ),
    )

    for agent_id, prompt, output_schema in configurations:
        validate_agent_configuration(
            registry,
            models,
            agent_id,
            prompt,
            resolved_tool_bindings[agent_id],
            output_schema,
        )

    router_app = create_agent(
        model=models[ModelPolicyId.ROUTER],
        tools=[],
        system_prompt=ROUTER_SYSTEM_PROMPT,
        response_format=RoutePlan,
    )

    farm_app = create_agent(
        model=models[ModelPolicyId.FARM],
        tools=tools_for(AgentId.FARM),
        system_prompt=FARM_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    doctor_app = create_agent(
        model=models[ModelPolicyId.DOCTOR],
        tools=tools_for(AgentId.DOCTOR),
        system_prompt=DOCTOR_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    analyst_app = create_agent(
        model=models[ModelPolicyId.ANALYST],
        tools=tools_for(AgentId.ANALYST),
        system_prompt=ANALYST_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    secretary_app = create_agent(
        model=models[ModelPolicyId.SECRETARY],
        tools=tools_for(AgentId.SECRETARY),
        system_prompt=SECRETARY_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    information_app = create_agent(
        model=models[ModelPolicyId.INFORMATION],
        tools=tools_for(AgentId.INFORMATION),
        system_prompt=INFORMATION_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    orchestrator_app = create_agent(
        model=models[ModelPolicyId.ORCHESTRATOR],
        tools=[],
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        response_format=OrchestratorResult,
    )

    judge_app = create_agent(
        model=models[ModelPolicyId.JUDGE],
        tools=[],
        system_prompt=JUDGE_SYSTEM_PROMPT,
        response_format=JudgeResult,
    )

    question_formatter_app = create_agent(
        model=models[ModelPolicyId.QUESTION_FORMATTER],
        tools=[],
        system_prompt=QUESTION_FORMATTER_SYSTEM_PROMPT,
        response_format=QuestionFormatterResult,
    )

    return LangChainAgentFactory(
        registry,
        {
            AgentId.ROUTER: router_app,
            AgentId.FARM: farm_app,
            AgentId.DOCTOR: doctor_app,
            AgentId.ANALYST: analyst_app,
            AgentId.SECRETARY: secretary_app,
            AgentId.INFORMATION: information_app,
            AgentId.ORCHESTRATOR: orchestrator_app,
            AgentId.JUDGE: judge_app,
            AgentId.QUESTION_FORMATTER: question_formatter_app,
        },
        execution_events=execution_events,
    )
