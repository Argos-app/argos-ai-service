import sys
from pathlib import Path
from typing import Any

import pytest
from langchain.tools import tool
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent_schemas.inputs import SpecialistInput
from app.agent_schemas.results import JudgeResult, QuestionFormatterResult, SpecialistResult
from app.agent_schemas.synthesis import OrchestratorResult
from app.agents import specialists
from app.agents.mock_tools import mock_consultar_fazenda
from app.agents.registry import ModelPolicyId
from app.agents.specialists import build_agent_factory
from app.domain.sessions import ExecutionStatus
from app.infrastructure.agents import langchain_factory
from app.infrastructure.mcp.client import McpClients
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


class _FakeChatModel:
    def with_structured_output(self, _schema):
        return self

    def bind_tools(self, _tools):
        return self


def _models() -> dict[ModelPolicyId, object]:
    return {policy: _FakeChatModel() for policy in ModelPolicyId}


@pytest.mark.asyncio
async def test_build_agent_factory_resolves_registry_prompt_and_mock_tool(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> _FakeRunnable:
        calls.append(kwargs)
        return _FakeRunnable()

    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)
    factory = build_agent_factory(_models(), use_mock_tools=True)

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
    factory = build_agent_factory(_models(), use_mock_tools=True)
    factory.create(AgentId.QUESTION_FORMATTER)

    formatter_call = next(
        call for call in calls if call["response_format"] is QuestionFormatterResult
    )
    assert formatter_call["tools"] == []
    assert "Formatador de Perguntas" in formatter_call["system_prompt"]


def test_build_agent_factory_accepts_real_tools_by_registered_slot(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> _FakeRunnable:
        calls.append(kwargs)
        return _FakeRunnable()

    @tool("get_lot_summary", description="Retorna um resumo do lote para testes.")
    def get_lot_summary(lot_id: str) -> str:
        return f"Resumo do lote {lot_id}"

    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)
    build_agent_factory(
        _models(),
        tool_bindings={AgentId.FARM: {"farm_data": (get_lot_summary,)}},
        use_mock_tools=True,
    )

    farm_call = next(
        call for call in calls if [item.name for item in call["tools"]] == ["get_lot_summary"]
    )
    assert farm_call["response_format"] is SpecialistResult


def test_build_agent_factory_rejects_tool_slot_not_allowed_for_agent() -> None:
    with pytest.raises(langchain_factory.AgentFactoryConfigurationError, match="não permitidos"):
        build_agent_factory(
            _models(),
            tool_bindings={
                AgentId.FARM: {"report_delivery": (mock_consultar_fazenda,)},
            },
            use_mock_tools=True,
        )


def test_build_agent_factory_rejects_unapproved_secretary_tool_name() -> None:
    with pytest.raises(
        langchain_factory.AgentFactoryConfigurationError, match="Tools não permitidas"
    ):
        build_agent_factory(
            _models(),
            tool_bindings={AgentId.SECRETARY: {"report_delivery": (mock_consultar_fazenda,)}},
            use_mock_tools=True,
        )


def test_build_agent_factory_does_not_silently_select_mock_tools() -> None:
    with pytest.raises(langchain_factory.AgentFactoryConfigurationError, match="incompletos"):
        build_agent_factory(_models())


def test_explicit_mcp_configuration_binds_unavailable_tools_instead_of_mocks(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> _FakeRunnable:
        calls.append(kwargs)
        return _FakeRunnable()

    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)
    build_agent_factory(_models(), mcp_clients=McpClients())

    assert any([tool.name for tool in call["tools"]] == ["mcp_email_unavailable"] for call in calls)
    assert all(not tool.name.startswith("mock_") for call in calls for tool in call["tools"])


def test_factory_fails_when_model_for_policy_is_missing() -> None:
    models = _models()
    del models[ModelPolicyId.FARM]

    with pytest.raises(langchain_factory.AgentFactoryConfigurationError, match="Modelo"):
        build_agent_factory(models, use_mock_tools=True)


@pytest.mark.asyncio
async def test_langchain_adapter_rejects_invalid_input(monkeypatch) -> None:
    monkeypatch.setattr(specialists, "create_agent", lambda **_: _FakeRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.FARM)

    with pytest.raises(ValidationError):
        await agent.run({"task_id": "", "goal": "", "brief_sanitized": ""})


@pytest.mark.asyncio
async def test_agent_passes_context_to_runnable(monkeypatch) -> None:
    runnable = _FakeRunnable()
    monkeypatch.setattr(specialists, "create_agent", lambda **_: runnable)

    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.FARM)
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
async def test_agent_persists_correlated_events_when_interaction_context_exists(
    monkeypatch,
) -> None:
    class FakeEventRepository:
        def __init__(self) -> None:
            self.events = []

        async def append(self, event) -> None:
            self.events.append(event)

    runnable = _FakeRunnable()
    repository = FakeEventRepository()
    monkeypatch.setattr(specialists, "create_agent", lambda **_: runnable)
    factory = build_agent_factory(_models(), execution_events=repository, use_mock_tools=True)

    await factory.create(AgentId.FARM).run(
        SpecialistInput(
            task_id="task-1",
            goal="Consultar registros",
            brief_sanitized="Contato user@example.invalid",
        ),
        context={
            "user_id": "user-demo",
            "farm_id": "farm-demo",
            "session_id": "a2d9c507-180a-45ac-a975-972cb303d279",
            "interaction_id": "8c613626-1a3e-43f0-a8a0-dc3591d734a4",
        },
    )

    assert [event.status for event in repository.events] == [
        ExecutionStatus.STARTED,
        ExecutionStatus.SUCCEEDED,
    ]
    assert repository.events[0].invocation_id == repository.events[1].invocation_id
    assert "user@example.invalid" not in repository.events[0].sanitized_input
    assert runnable.last_config["callbacks"]


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
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.FARM)

    result = await agent.run(
        SpecialistInput(
            task_id="task-1", goal="Consultar aplicações", brief_sanitized="Consultar registros"
        ),
        context={"farm_id": "farm-demo"},
    )

    assert "SIMULAÇÃO" in result.answer
    assert "mock://farm-demo/aplicacoes/1" in result.answer
