import sys
from pathlib import Path
from typing import Any

import pytest
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent_schemas.inputs import SpecialistInput
from app.agent_schemas.results import (
    JudgeResult,
    QuestionFormatterResult,
    RoutePlan,
    RouteTask,
    SourceReference,
    SpecialistResult,
)
from app.agent_schemas.synthesis import (
    JudgeInput,
    OrchestratorInput,
    OrchestratorResult,
    TaskOutcome,
)
from app.agents import specialists
from app.agents.mock_tools import mock_consultar_fazenda, mock_consultar_legal
from app.agents.registry import ModelPolicyId
from app.agents.specialists import build_agent_factory
from app.application.execution_limits import ExecutionLimits
from app.domain.sessions import ExecutionStatus
from app.infrastructure.agents import langchain_factory
from app.infrastructure.agents.langchain_factory import AgentResponseError
from app.infrastructure.agents.model_factory import ModelRuntimeConfigurationError
from app.infrastructure.agents.simulated_model import SimulatedChatModel
from app.infrastructure.mcp.client import McpClients
from app.ports.agent import AgentId


def test_model_output_contracts_ignore_unknown_provider_fields() -> None:
    route = RoutePlan.model_validate({"intent": "lookup", "provider_metadata": {"x": 1}})
    synthesis = OrchestratorResult.model_validate(
        {"draft_answer": "Verified answer", "provider_metadata": {"x": 1}}
    )
    judge = JudgeResult.model_validate(
        {
            "decision": "approved",
            "reason": "Supported by evidence",
            "provider_metadata": {"x": 1},
        }
    )

    assert route.intent == "lookup"
    assert synthesis.draft_answer == "Verified answer"
    assert judge.decision.value == "approved"

    aliased_synthesis = OrchestratorResult.model_validate({"answer": "Another answer"})
    aliased_judge = JudgeResult.model_validate(
        {"verdict": "approve", "feedback": "Evidence supports the answer.", "unsupported": ""}
    )
    aliased_specialist = SpecialistResult.model_validate(
        {"agent": "farm", "status": "success", "response": "Verified result"}
    )
    assert aliased_synthesis.draft_answer == "Another answer"
    assert aliased_judge.decision.value == "approved"
    assert aliased_judge.reason == "Evidence supports the answer."
    assert aliased_specialist.answer == "Verified result"


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
    assert farm_call["response_format"].schema is SpecialistResult
    information_call = next(
        call
        for call in calls
        if [tool.name for tool in call["tools"]]
        == ["mock_consultar_documental", "mock_consultar_legal"]
    )
    assert information_call["system_prompt"]
    structured_calls = {
        call["response_format"].schema: call
        for call in calls
        if isinstance(call.get("response_format"), ToolStrategy)
    }
    structured_router = structured_calls[RoutePlan]
    assert structured_router["response_format"].handle_errors is False
    orchestrator_call = next(
        call for call in calls if "Você é o Orquestrador do sistema." in call["system_prompt"]
    )
    assert "response_format" not in orchestrator_call
    assert all(
        isinstance(call["response_format"], ToolStrategy)
        for call in calls
        if call is structured_router
    )
    specialists_with_tools = [
        call
        for call in calls
        if isinstance(call.get("response_format"), ToolStrategy)
        and call["response_format"].schema is SpecialistResult
    ]
    assert len(specialists_with_tools) == 5
    assert all(call["response_format"].handle_errors is False for call in specialists_with_tools)
    judge_call = next(call for call in calls if "Você é o Juiz do Argos." in call["system_prompt"])
    assert "response_format" not in judge_call

    agent = factory.create(AgentId.FARM)
    result = await agent.run(
        SpecialistInput(
            task_id="task-1",
            goal="Consultar registros",
            brief_sanitized="Consultar registros permitidos",
        )
    )
    assert result.answer == "Resultado de teste"


def test_simulated_factory_uses_structured_outputs_for_orchestrator_and_judge(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> _FakeRunnable:
        calls.append(kwargs)
        return _FakeRunnable()

    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)
    simulated_model = SimulatedChatModel()
    models = {policy: simulated_model for policy in ModelPolicyId}
    build_agent_factory(models, use_mock_tools=True)

    orchestrator_call = next(
        call for call in calls if "Você é o Orquestrador do sistema." in call["system_prompt"]
    )
    judge_call = next(call for call in calls if "Você é o Juiz do Argos." in call["system_prompt"])
    assert orchestrator_call["response_format"].schema is OrchestratorResult
    assert judge_call["response_format"].schema is JudgeResult


def test_build_agent_factory_registers_question_formatter_without_tools(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> _FakeRunnable:
        calls.append(kwargs)
        return _FakeRunnable()

    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)
    factory = build_agent_factory(_models(), use_mock_tools=True)
    factory.create(AgentId.QUESTION_FORMATTER)

    formatter_call = next(
        call for call in calls if call.get("response_format") is QuestionFormatterResult
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
    assert farm_call["response_format"].schema is SpecialistResult


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

    with pytest.raises(ModelRuntimeConfigurationError, match="modelo ausente"):
        build_agent_factory(models, use_mock_tools=True)


@pytest.mark.asyncio
async def test_langchain_adapter_rejects_invalid_input(monkeypatch) -> None:
    monkeypatch.setattr(specialists, "create_agent", lambda **_: _FakeRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.FARM)

    with pytest.raises(ValidationError):
        await agent.run({"task_id": "", "goal": "", "brief_sanitized": ""})


@pytest.mark.asyncio
async def test_agent_accepts_complete_json_text_when_provider_omits_structured_response(
    monkeypatch,
) -> None:
    class JsonTextRunnable:
        async def ainvoke(self, _state, config=None):
            return {
                "messages": [
                    {
                        "role": "assistant",
                        "content": (
                            '```json\n{"agent":"farm","status":"success",'
                            '"answer":"49 bovinos","provider_metadata":{"retry":1}}\n```'
                        ),
                    }
                ]
            }

    monkeypatch.setattr(specialists, "create_agent", lambda **_: JsonTextRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.FARM)

    result = await agent.run(
        SpecialistInput(
            task_id="task-1",
            goal="Consultar registros",
            brief_sanitized="Consultar registros permitidos",
        )
    )

    assert result.answer == "49 bovinos"


@pytest.mark.asyncio
async def test_agent_does_not_treat_free_text_as_structured_success(monkeypatch) -> None:
    class PlainTextRunnable:
        async def ainvoke(self, _state, config=None):
            return {"messages": [{"role": "assistant", "content": "There are 49 bovines."}]}

    monkeypatch.setattr(specialists, "create_agent", lambda **_: PlainTextRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.FARM)

    with pytest.raises(AgentResponseError, match="JSON estruturado"):
        await agent.run(
            SpecialistInput(
                task_id="task-1",
                goal="Consultar registros",
                brief_sanitized="Consultar registros permitidos",
            )
        )


@pytest.mark.asyncio
async def test_specialist_agent_identity_is_inferred_from_its_registered_agent(monkeypatch):
    class MissingAgentFieldRunnable:
        async def ainvoke(self, _state, config=None):
            return {
                "structured_response": {
                    "status": "success",
                    "response": "Lote consultado.",
                    "provider_metadata": {"elapsed": 1},
                }
            }

    monkeypatch.setattr(specialists, "create_agent", lambda **_: MissingAgentFieldRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.FARM)

    result = await agent.run(
        SpecialistInput(
            task_id="farm-1",
            goal="Consultar lote",
            brief_sanitized="Consultar os dados do lote",
        )
    )

    assert result.agent.value == "farm"
    assert result.answer == "Lote consultado."


@pytest.mark.asyncio
async def test_orchestrator_accepts_plain_text_and_attaches_only_received_sources(monkeypatch):
    class PlainTextRunnable:
        async def ainvoke(self, _state, config=None):
            return {"messages": [{"role": "assistant", "content": "A fazenda tem 49 bovinos."}]}

    monkeypatch.setattr(specialists, "create_agent", lambda **_: PlainTextRunnable())
    source = SourceReference(
        title="Lote",
        reference="tool-call:lot-1",
        excerpt='{"head_count":49}',
    )
    outcome = TaskOutcome(
        task=RouteTask(
            task_id="farm-1",
            agent="farm",
            goal="Consultar lote",
            brief_sanitized="Consultar dados do lote",
        ),
        result=SpecialistResult(
            agent="farm",
            status="success",
            answer="49 bovinos",
            sources=(source,),
        ),
    )
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.ORCHESTRATOR)

    result = await agent.run(OrchestratorInput(request="Quantos bovinos?", outcomes=(outcome,)))

    assert result.draft_answer == "A fazenda tem 49 bovinos."
    assert result.sources == (source,)


@pytest.mark.asyncio
async def test_judge_accepts_only_explicit_plain_text_verdict(monkeypatch):
    class PlainTextRunnable:
        async def ainvoke(self, _state, config=None):
            return {"messages": [{"role": "assistant", "content": "decision: approved"}]}

    monkeypatch.setattr(specialists, "create_agent", lambda **_: PlainTextRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.JUDGE)

    result = await agent.run(JudgeInput(request="Consulta", draft_answer="Resposta"))

    assert result.decision.value == "approved"
    assert result.reason == "Veredito textual explícito do Juiz."


@pytest.mark.asyncio
async def test_judge_accepts_json_text_without_provider_structured_response(monkeypatch):
    class JsonTextRunnable:
        async def ainvoke(self, _state, config=None):
            return {
                "messages": [
                    {
                        "role": "assistant",
                        "content": (
                            '{"verdict":"approved",'
                            '"rationale":"Os valores coincidem com a fonte.",'
                            '"extra":"ignorado"}'
                        ),
                    }
                ]
            }

    monkeypatch.setattr(specialists, "create_agent", lambda **_: JsonTextRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.JUDGE)

    result = await agent.run(JudgeInput(request="Consulta", draft_answer="Resposta"))

    assert result.decision.value == "approved"
    assert result.reason == "Os valores coincidem com a fonte."


@pytest.mark.asyncio
async def test_judge_uses_explicit_verdict_when_provider_structured_payload_is_invalid(monkeypatch):
    class MixedResponseRunnable:
        async def ainvoke(self, _state, config=None):
            return {
                "structured_response": {"decision": "yes"},
                "messages": [
                    {
                        "role": "assistant",
                        "content": "decision: approved - A resposta corresponde às evidências.",
                    }
                ],
            }

    monkeypatch.setattr(specialists, "create_agent", lambda **_: MixedResponseRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.JUDGE)

    result = await agent.run(JudgeInput(request="Consulta", draft_answer="Resposta"))

    assert result.decision.value == "approved"
    assert "corresponde" in result.reason


@pytest.mark.asyncio
async def test_judge_rejects_ambiguous_plain_text(monkeypatch):
    class PlainTextRunnable:
        async def ainvoke(self, _state, config=None):
            return {"messages": [{"role": "assistant", "content": "This looks okay."}]}

    monkeypatch.setattr(specialists, "create_agent", lambda **_: PlainTextRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.JUDGE)

    with pytest.raises(AgentResponseError, match="veredito explícito"):
        await agent.run(JudgeInput(request="Consulta", draft_answer="Resposta"))


@pytest.mark.asyncio
async def test_agent_accepts_plain_text_only_after_matching_tool_evidence(monkeypatch) -> None:
    class PlainTextRunnable:
        async def ainvoke(self, _state, config=None):
            return {"messages": [{"role": "assistant", "content": "Estimativa: 52,9 ml."}]}

    monkeypatch.setattr(specialists, "create_agent", lambda **_: PlainTextRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.ANALYST)
    limits = ExecutionLimits(max_model_calls=5, max_tool_calls=5)
    limits.record_tool_success(
        "analyst",
        "estimate_medicine_consumption",
        '{"status":"estimated","items":[{"estimated_quantity":"52.9"}]}',
        reference="tool-call:prediction-1",
    )

    result = await agent.run(
        SpecialistInput(
            task_id="task-1",
            goal="Estimar consumo",
            brief_sanitized="Estimar consumo da Vacina Teste A",
        ),
        context={"argos_execution_limits": limits},
    )

    assert result.status.value == "success"
    assert result.answer == "Estimativa: 52,9 ml."


@pytest.mark.asyncio
async def test_agent_uses_tool_evidence_when_provider_returns_empty_final_message(
    monkeypatch,
) -> None:
    class EmptyFinalRunnable:
        async def ainvoke(self, _state, config=None):
            return {"messages": [{"role": "assistant", "content": ""}]}

    monkeypatch.setattr(specialists, "create_agent", lambda **_: EmptyFinalRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.ANALYST)
    limits = ExecutionLimits(max_model_calls=5, max_tool_calls=5)
    limits.record_tool_success(
        "analyst",
        "estimate_medicine_consumption",
        '{"status":"estimated","forecast_window":{"start":"2026-10-16",'
        '"end":"2026-11-12"},"coverage":[{"status":"confirmed"}],"items":['
        '{"medicine_name":"Vacina Teste A","unit":"ml","estimated_quantity":"52.9",'
        '"estimated_application_events":"4.2","estimated_head_administrations":"42"}]}',
        reference="tool-call:prediction-1",
    )

    result = await agent.run(
        SpecialistInput(
            task_id="task-1",
            goal="Estimar consumo",
            brief_sanitized="Estimar consumo da Vacina Teste A",
        ),
        context={"argos_execution_limits": limits},
    )

    assert result.status.value == "success"
    assert "fonte verificada" in result.answer
    assert '"estimated_quantity":"52.9"' in result.sources[0].excerpt


@pytest.mark.asyncio
async def test_analyst_does_not_repeat_property_question_already_answered_by_mcp(monkeypatch):
    class ClarifyingRunnable:
        async def ainvoke(self, _state, config=None):
            return {
                "structured_response": {
                    "status": "needs_input",
                    "missing_fields": ["propriedade"],
                    "response": "Qual propriedade devo usar?",
                }
            }

    monkeypatch.setattr(specialists, "create_agent", lambda **_: ClarifyingRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.ANALYST)
    limits = ExecutionLimits(max_model_calls=5, max_tool_calls=5)
    limits.record_tool_success(
        "analyst",
        "list_properties",
        '{"items":[{"property_id":"property-1","property_name":"Fazenda Sol Nascente"}]}',
        reference="tool-call:properties-1",
    )
    limits.record_tool_success(
        "analyst",
        "estimate_medicine_consumption",
        '{"status":"estimated","forecast_window":{"start":"2026-10-16",'
        '"end":"2026-11-12"},"coverage":[{"status":"confirmed"}],"items":['
        '{"medicine_name":"Vacina Teste A","unit":"ml","estimated_quantity":"52.9",'
        '"estimated_application_events":"4.2","estimated_head_administrations":"42"}]}',
        reference="tool-call:prediction-1",
    )

    result = await agent.run(
        SpecialistInput(
            task_id="analyst-1",
            request="Prediga para Fazenda Sol Nascente.",
            goal="Estimar consumo",
            brief_sanitized="Estimar o consumo da Vacina Teste A.",
        ),
        context={"argos_execution_limits": limits},
    )

    assert result.status.value == "success"
    assert '"estimated_quantity":"52.9"' in result.sources[0].excerpt


@pytest.mark.asyncio
async def test_unavailable_specialist_uses_matching_verified_tool_evidence(monkeypatch) -> None:
    class AbstainingRunnable:
        async def ainvoke(self, _state, config=None):
            return {
                "structured_response": SpecialistResult(
                    agent="information",
                    status="unavailable",
                    answer="A consulta não pôde ser interpretada.",
                )
            }

    monkeypatch.setattr(specialists, "create_agent", lambda **_: AbstainingRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.INFORMATION)
    limits = ExecutionLimits(max_model_calls=5, max_tool_calls=5)
    limits.record_tool_success(
        "information",
        "consultar_legal",
        '{"status":"partial","sources":[{"title":"Lei sintética",'
        '"reference":"local://lei-1","excerpt":"Art. 1: registrar data e produto."}]}',
        reference="tool-call:legal-1",
    )

    result = await agent.run(
        SpecialistInput(
            task_id="task-legal",
            goal="Consultar regra de registro",
            brief_sanitized="Quais dados registrar?",
        ),
        context={"argos_execution_limits": limits},
    )

    assert result.status.value == "partial"
    assert "Art. 1: registrar data e produto" in result.answer
    assert result.sources[0].reference == "tool-call:legal-1"


@pytest.mark.asyncio
async def test_agent_does_not_request_data_already_returned_by_a_tool(monkeypatch) -> None:
    class ClarifyingRunnable:
        async def ainvoke(self, _state, config=None):
            return {
                "structured_response": SpecialistResult(
                    agent="doctor",
                    status="needs_clarification",
                    answer="Pendente consulta da ficha documental.",
                    missing_fields=("ficha documental",),
                )
            }

    monkeypatch.setattr(specialists, "create_agent", lambda **_: ClarifyingRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.DOCTOR)
    limits = ExecutionLimits(max_model_calls=5, max_tool_calls=5)
    limits.record_tool_success(
        "doctor",
        "list_medicines",
        '{"content":{"items":[{"active_ingredient":"Ivermectina","dose":"10.000"}]}}',
        reference="tool-call:medicine-catalog-1",
    )
    limits.record_tool_success(
        "doctor",
        "list_medicine_documents",
        '{"content":{"items":[{"active_ingredient":"Ivermectina","dose":"10.000","unit":"ml"}]}}',
        reference="tool-call:medicine-document-1",
    )

    result = await agent.run(
        SpecialistInput(
            task_id="task-medicine",
            goal="Consultar princípio ativo e dose",
            brief_sanitized="Pesquisar Ivervet Teste",
        ),
        context={"argos_execution_limits": limits},
    )

    assert result.status.value == "partial"
    assert "Ivermectina" in result.answer
    assert {source.reference for source in result.sources} == {
        "tool-call:medicine-catalog-1",
        "tool-call:medicine-document-1",
    }


@pytest.mark.asyncio
async def test_information_agent_can_be_built_with_both_document_and_legal_mocks(
    monkeypatch,
) -> None:
    monkeypatch.setattr(specialists, "create_agent", lambda **_: _FakeRunnable())
    factory = build_agent_factory(_models(), use_mock_tools=True)
    assert factory.create(AgentId.INFORMATION) is not None

    result = await mock_consultar_legal.ainvoke(
        {"question": "Quais campos registrar?", "jurisdiction": "BR"}
    )
    assert "Nenhuma lei foi consultada" in result


@pytest.mark.asyncio
async def test_analyst_plain_text_requires_prediction_evidence(monkeypatch) -> None:
    class PlainTextRunnable:
        async def ainvoke(self, _state, config=None):
            return {"messages": [{"role": "assistant", "content": "Estimativa: 52,9 ml."}]}

    monkeypatch.setattr(specialists, "create_agent", lambda **_: PlainTextRunnable())
    agent = build_agent_factory(_models(), use_mock_tools=True).create(AgentId.ANALYST)
    limits = ExecutionLimits(max_model_calls=5, max_tool_calls=5)
    limits.record_tool_success(
        "analyst",
        "list_properties",
        '{"items":[{"property_id":"00000000-0000-4000-8000-000000000001"}]}',
        reference="tool-call:properties-1",
    )

    with pytest.raises(AgentResponseError, match="evidência suficiente"):
        await agent.run(
            SpecialistInput(
                task_id="task-1",
                goal="Estimar consumo",
                brief_sanitized="Estimar consumo da Vacina Teste A",
            ),
            context={"argos_execution_limits": limits},
        )


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
    assert "user@example.invalid" in repository.events[0].sanitized_input
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
