"""Criação explícita dos agentes do serviço."""

from collections.abc import Mapping
from typing import Any

from app.agent_schemas.results import (
    QuestionFormatterResult,
    RoutePlan,
    SpecialistResult,
)
from app.agents.mock_tools import (
    MOCK_ANALYST_TOOLS,
    MOCK_DOCTOR_TOOLS,
    MOCK_FARM_TOOLS,
    MOCK_INFORMATION_TOOLS,
    MOCK_SECRETARY_TOOLS,
)
from app.agents.registry import DEFAULT_AGENT_REGISTRY, AgentRegistry, ModelPolicyId
from app.infrastructure.agents.langchain_factory import (
    LangChainAgentFactory,
    validate_agent_configuration,
)
from app.ports.agent import AgentId
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


def build_agent_factory(
    models: Mapping[ModelPolicyId, Any],
    registry: AgentRegistry = DEFAULT_AGENT_REGISTRY,
) -> LangChainAgentFactory:
    """Valida as definições e cria cada agente explicitamente."""

    configurations = (
        (AgentId.ROUTER, ROUTER_SYSTEM_PROMPT, [], RoutePlan),
        (AgentId.FARM, FARM_SYSTEM_PROMPT, MOCK_FARM_TOOLS, SpecialistResult),
        (AgentId.DOCTOR, DOCTOR_SYSTEM_PROMPT, MOCK_DOCTOR_TOOLS, SpecialistResult),
        (AgentId.ANALYST, ANALYST_SYSTEM_PROMPT, MOCK_ANALYST_TOOLS, SpecialistResult),
        (AgentId.SECRETARY, SECRETARY_SYSTEM_PROMPT, MOCK_SECRETARY_TOOLS, SpecialistResult),
        (AgentId.INFORMATION, INFORMATION_SYSTEM_PROMPT, MOCK_INFORMATION_TOOLS, SpecialistResult),
        (
            AgentId.QUESTION_FORMATTER,
            QUESTION_FORMATTER_SYSTEM_PROMPT,
            [],
            QuestionFormatterResult,
        ),
    )

    for agent_id, prompt, tools, output_schema in configurations:
        validate_agent_configuration(
            registry,
            models,
            agent_id,
            prompt,
            tools,
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
        tools=MOCK_FARM_TOOLS,
        system_prompt=FARM_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    doctor_app = create_agent(
        model=models[ModelPolicyId.DOCTOR],
        tools=MOCK_DOCTOR_TOOLS,
        system_prompt=DOCTOR_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    analyst_app = create_agent(
        model=models[ModelPolicyId.ANALYST],
        tools=MOCK_ANALYST_TOOLS,
        system_prompt=ANALYST_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    secretary_app = create_agent(
        model=models[ModelPolicyId.SECRETARY],
        tools=MOCK_SECRETARY_TOOLS,
        system_prompt=SECRETARY_SYSTEM_PROMPT,
        response_format=SpecialistResult,
    )

    information_app = create_agent(
        model=models[ModelPolicyId.INFORMATION],
        tools=MOCK_INFORMATION_TOOLS,
        system_prompt=INFORMATION_SYSTEM_PROMPT,
        response_format=SpecialistResult,
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
            AgentId.QUESTION_FORMATTER: question_formatter_app,
        },
    )
