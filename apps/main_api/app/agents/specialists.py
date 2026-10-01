"""Composição da Factory com os prompts e ferramentas do serviço."""

from collections.abc import Mapping
from typing import Any

from app.agents.mock_tools import (
    MOCK_ANALYST_TOOLS,
    MOCK_DOCTOR_TOOLS,
    MOCK_FARM_TOOLS,
    MOCK_INFORMATION_TOOLS,
    MOCK_SECRETARY_TOOLS,
)
from app.agents.registry import DEFAULT_AGENT_REGISTRY, AgentRegistry, ModelPolicyId
from app.infrastructure.agents.langchain_factory import LangChainAgentFactory
from app.prompts.question_formatter import QUESTION_FORMATTER_SYSTEM_PROMPT
from app.prompts.router import ROUTER_SYSTEM_PROMPT
from app.prompts.specialists import SPECIALIST_PROMPTS


def build_agent_factory(
    models: Mapping[ModelPolicyId, Any],
    registry: AgentRegistry = DEFAULT_AGENT_REGISTRY,
) -> LangChainAgentFactory:
    """Monta a Factory injetando modelos, prompts e ferramentas disponíveis."""
    prompts = {
        ("router", "1"): ROUTER_SYSTEM_PROMPT,
        **{(agent_id, "1"): prompt for agent_id, prompt in SPECIALIST_PROMPTS.items()},
        ("question_formatter", "1"): QUESTION_FORMATTER_SYSTEM_PROMPT,
    }

    available_tools = (
        *MOCK_FARM_TOOLS,
        *MOCK_DOCTOR_TOOLS,
        *MOCK_ANALYST_TOOLS,
        *MOCK_SECRETARY_TOOLS,
        *MOCK_INFORMATION_TOOLS,
    )
    tools: dict[str, Any] = {}
    for tool in available_tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Toda ferramenta precisa declarar um nome não vazio")
        if name in tools and tools[name] is not tool:
            raise ValueError(f"Ferramenta duplicada na composição: {name}")
        tools.setdefault(name, tool)

    return LangChainAgentFactory(registry, models, prompts, tools)
