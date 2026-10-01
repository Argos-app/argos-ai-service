"""Testes da composição e do adaptador LangChain de agentes."""

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent_schemas.inputs import SpecialistInput
from app.agent_schemas.results import QuestionFormatterResult, SpecialistResult
from app.agents.registry import ModelPolicyId
from app.agents.specialists import build_agent_factory
from app.infrastructure.agents import langchain_factory
from app.ports.agent import AgentId


class _FakeRunnable:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        assert state["messages"]
        return {
            "structured_response": SpecialistResult(
                agent="farm",
                status="success",
                answer="Resultado de teste",
            )
        }


def _models() -> dict[ModelPolicyId, object]:
    return {policy: object() for policy in ModelPolicyId}


@pytest.mark.asyncio
async def test_build_agent_factory_resolves_registry_prompt_and_mock_tool(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> _FakeRunnable:
        calls.append(kwargs)
        return _FakeRunnable()

    monkeypatch.setattr(langchain_factory, "create_agent", fake_create_agent)
    factory = build_agent_factory(_models())

    agent = factory.create(AgentId.FARM)
    assert len(calls) == 1
    assert calls[0]["system_prompt"]
    assert calls[0]["response_format"] is SpecialistResult
    assert [tool.name for tool in calls[0]["tools"]] == ["mock_consultar_fazenda"]

    result = await agent.run(
        SpecialistInput(
            task_id="task-1",
            goal="Consultar registros",
            brief_sanitized="Consultar registros permitidos",
        )
    )
    assert result.answer == "Resultado de teste"


def test_build_agent_factory_registers_question_formatter_without_tools(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> _FakeRunnable:
        calls.append(kwargs)
        return _FakeRunnable()

    monkeypatch.setattr(langchain_factory, "create_agent", fake_create_agent)
    factory = build_agent_factory(_models())
    factory.create(AgentId.QUESTION_FORMATTER)

    assert calls[0]["tools"] == []
    assert calls[0]["response_format"] is QuestionFormatterResult
    assert "Formatador de Perguntas" in calls[0]["system_prompt"]


def test_factory_fails_when_model_for_policy_is_missing() -> None:
    models = _models()
    del models[ModelPolicyId.FARM]
    factory = build_agent_factory(models)

    with pytest.raises(langchain_factory.AgentFactoryConfigurationError, match="Modelo"):
        factory.create(AgentId.FARM)


@pytest.mark.asyncio
async def test_langchain_adapter_rejects_invalid_input(monkeypatch) -> None:
    monkeypatch.setattr(langchain_factory, "create_agent", lambda **_: _FakeRunnable())
    agent = build_agent_factory(_models()).create(AgentId.FARM)

    with pytest.raises(ValidationError):
        await agent.run({"task_id": "", "goal": "", "brief_sanitized": ""})
