"""Testes da composição e do adaptador LangChain de agentes."""

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent_schemas.inputs import SpecialistInput
from app.agent_schemas.results import JudgeResult, QuestionFormatterResult, SpecialistResult
from app.agent_schemas.synthesis import OrchestratorResult
from app.agents import specialists
from app.agents.mock_tools import mock_consultar_fazenda
from app.agents.registry import ModelPolicyId
from app.agents.specialists import build_agent_factory
from app.infrastructure.agents import langchain_factory
from app.ports.agent import AgentId


class _FakeRunnable:
    def __init__(self) -> None:
        self.last_config: dict[str, Any] | None = None

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert state["messages"]
        self.last_config = config
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

    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)
    factory = build_agent_factory(_models())

    assert len(calls) == 9
    farm_call = next(
        call
        for call in calls
        if [tool.name for tool in call["tools"]] == ["mock_consultar_fazenda"]
    )
    assert farm_call["system_prompt"]
    assert farm_call["response_format"] is SpecialistResult
    assert any(call["response_format"] is OrchestratorResult for call in calls)
    assert any(call["response_format"] is JudgeResult for call in calls)

    agent = factory.create(AgentId.FARM)
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

    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)
    factory = build_agent_factory(_models())
    factory.create(AgentId.QUESTION_FORMATTER)

    formatter_call = next(
        call for call in calls if call["response_format"] is QuestionFormatterResult
    )
    assert formatter_call["tools"] == []
    assert "Formatador de Perguntas" in formatter_call["system_prompt"]


def test_factory_fails_when_model_for_policy_is_missing() -> None:
    models = _models()
    del models[ModelPolicyId.FARM]

    with pytest.raises(langchain_factory.AgentFactoryConfigurationError, match="Modelo"):
        build_agent_factory(models)


@pytest.mark.asyncio
async def test_langchain_adapter_rejects_invalid_input(monkeypatch) -> None:
    monkeypatch.setattr(specialists, "create_agent", lambda **_: _FakeRunnable())
    agent = build_agent_factory(_models()).create(AgentId.FARM)

    with pytest.raises(ValidationError):
        await agent.run({"task_id": "", "goal": "", "brief_sanitized": ""})


@pytest.mark.asyncio
async def test_agent_passes_context_to_runnable(monkeypatch) -> None:
    runnable = _FakeRunnable()
    monkeypatch.setattr(specialists, "create_agent", lambda **_: runnable)

    agent = build_agent_factory(_models()).create(AgentId.FARM)
    await agent.run(
        SpecialistInput(
            task_id="task-1",
            goal="Consultar registros",
            brief_sanitized="Consultar registros permitidos",
        ),
        context={
            "user_id": "user-demo",
            "farm_id": "farm-demo",
            "session_id": "session-demo",
        },
    )

    assert runnable.last_config == {
        "configurable": {
            "user_id": "user-demo",
            "farm_id": "farm-demo",
            "session_id": "session-demo",
        }
    }


@pytest.mark.asyncio
async def test_farm_agent_adapter_can_call_mock_tool_with_farm_context(monkeypatch) -> None:
    class ToolCallingFakeRunnable:
        async def ainvoke(
            self,
            state: dict[str, Any],
            config: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            assert state["messages"]
            answer = mock_consultar_fazenda.invoke({"consulta": "aplicações"}, config=config)
            return {
                "structured_response": SpecialistResult(
                    agent="farm", status="success", answer=answer
                )
            }

    monkeypatch.setattr(specialists, "create_agent", lambda **_: ToolCallingFakeRunnable())
    agent = build_agent_factory(_models()).create(AgentId.FARM)

    result = await agent.run(
        SpecialistInput(
            task_id="task-1", goal="Consultar aplicações", brief_sanitized="Consultar registros"
        ),
        context={"farm_id": "farm-demo"},
    )

    assert "SIMULAÇÃO" in result.answer
    assert "mock://farm-demo/aplicacoes/1" in result.answer
