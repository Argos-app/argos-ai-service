"""Testes do catálogo declarativo de agentes."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agents.registry import (
    DEFAULT_AGENT_REGISTRY,
    AgentNotRegisteredError,
    AgentRegistry,
)
from app.ports.agent import AgentId


def test_default_registry_contains_routable_agents_and_question_formatter() -> None:
    assert DEFAULT_AGENT_REGISTRY.agent_ids == frozenset(
        {
            AgentId.ROUTER,
            AgentId.FARM,
            AgentId.DOCTOR,
            AgentId.ANALYST,
            AgentId.SECRETARY,
            AgentId.INFORMATION,
            AgentId.ORCHESTRATOR,
            AgentId.JUDGE,
            AgentId.QUESTION_FORMATTER,
        }
    )
    assert DEFAULT_AGENT_REGISTRY.get(AgentId.QUESTION_FORMATTER).tool_slots == ()


def test_registry_rejects_unknown_ids() -> None:
    with pytest.raises(AgentNotRegisteredError):
        DEFAULT_AGENT_REGISTRY.get("made_up_agent")


def test_registry_rejects_duplicate_ids() -> None:
    definition = DEFAULT_AGENT_REGISTRY.get(AgentId.FARM)

    with pytest.raises(ValueError, match="duplicado"):
        AgentRegistry((definition, definition))


def test_registry_rejects_incompatible_schemas_tools_and_model_policy() -> None:
    farm = DEFAULT_AGENT_REGISTRY.get(AgentId.FARM)
    router = DEFAULT_AGENT_REGISTRY.get(AgentId.ROUTER)

    with pytest.raises(TypeError, match="Schemas incompatíveis"):
        AgentRegistry((replace(farm, output_schema=router.output_schema),))

    with pytest.raises(ValueError, match="Slot de ferramenta não permitido"):
        AgentRegistry((replace(farm, tool_slots=("report_delivery",)),))

    with pytest.raises(ValueError, match="Política de modelo incompatível"):
        AgentRegistry((replace(farm, model_policy=router.model_policy),))
