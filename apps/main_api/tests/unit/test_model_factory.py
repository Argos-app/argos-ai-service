"""Configuração e capacidades do runtime de modelos do principal."""

import json
import sys
from dataclasses import replace

import pytest
from app.agent_schemas.results import RoutePlan, SpecialistAgentId
from app.agents.registry import ModelPolicyId
from app.infrastructure.agents.model_factory import (
    ModelRuntimeConfigurationError,
    _attach_anthropic_fallbacks,
    _CrossProviderChatModel,
    create_configured_models,
    validate_model_capabilities,
)
from app.infrastructure.agents.session_summary_generator import LangChainSessionSummaryGenerator
from app.infrastructure.agents.simulated_model import SimulatedChatModel
from app.settings import Settings
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from packages.contracts.prediction import EstimateMedicineConsumptionRequest


class _BoundModel:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def _runnable(self):
        def invoke(_input):
            if self.error is not None:
                raise self.error
            return self.result

        return RunnableLambda(invoke)

    def with_structured_output(self, schema, **kwargs):
        self.calls.append(("structured", schema, kwargs))
        return self._runnable()

    def bind_tools(self, tools, **kwargs):
        self.calls.append(("tools", tools, kwargs))
        return self._runnable()


class _StructuredAnswer(BaseModel):
    answer: str


def test_cross_provider_model_uses_native_structured_output_fallback():
    primary = _BoundModel(error=RuntimeError("OpenAI unavailable"))
    fallback = _BoundModel(result={"answer": "ok"})
    model = _CrossProviderChatModel(primary, fallback, (RuntimeError,))

    result = model.with_structured_output(dict).invoke("input")

    assert result == {"answer": "ok"}
    assert fallback.calls[0][0] == "structured"
    assert fallback.calls[0][2]["method"] == "json_schema"


def test_cross_provider_model_counts_tokens_for_both_providers():
    class TokenCountingModel(_BoundModel):
        def __init__(self, token_count):
            super().__init__()
            self.token_count = token_count

        def get_num_tokens(self, _text):
            return self.token_count

    model = _CrossProviderChatModel(
        TokenCountingModel(8),
        TokenCountingModel(13),
        (RuntimeError,),
    )

    assert model.get_num_tokens("summary input") == 13
    assert LangChainSessionSummaryGenerator(model, input_token_limit=1_000)


def test_cross_provider_model_binds_tools_on_both_providers_without_agent_replay():
    primary = _BoundModel(error=RuntimeError("OpenAI unavailable"))
    fallback = _BoundModel(result="tool call")
    model = _CrossProviderChatModel(primary, fallback, (RuntimeError,))
    tools = [object()]

    result = model.bind_tools(tools).invoke("input")

    assert result == "tool call"
    assert primary.calls == [("tools", tools, {})]
    assert fallback.calls == [("tools", tools, {"strict": True})]


def test_cross_provider_runnable_is_accepted_by_langgraph_agent_factory():
    model = _CrossProviderChatModel(
        _BoundModel(result={"answer": "primary"}),
        _BoundModel(result={"answer": "fallback"}),
        (RuntimeError,),
    )

    agent = create_agent(
        model=model,
        tools=[],
        response_format=ProviderStrategy(_StructuredAnswer, strict=True),
    )

    assert agent is not None


def _configured_model_names(**overrides):
    return {
        f"model_{policy.value}_name": overrides.get(policy.value, f"{policy.value}-test")
        for policy in ModelPolicyId
    }


class _FakeChatModel:
    def __init__(self, *, model, **options):
        self.model = model
        self.options = options

    def with_structured_output(self, _schema):
        return self

    def bind_tools(self, _tools):
        return self


def test_models_are_unavailable_until_real_provider_credentials_are_configured():
    models, reason = create_configured_models(
        Settings(
            openai_base_url="https://openai.example/v1",
            model_fast_name="fast-test",
            model_specialist_name="specialist-test",
        )
    )

    assert models is None
    assert reason == "model_credentials_not_configured"


def test_partial_model_configuration_is_reported_without_starting_provider():
    models, reason = create_configured_models(Settings(model_fast_name="fast-test"))

    assert models is None
    assert reason == "model_configuration_incomplete"


def test_provider_model_runtime_requires_endpoint_environment_value():
    models, reason = create_configured_models(
        Settings(
            openai_api_key="test-secret",
            model_fast_name="fast-test",
            model_specialist_name="specialist-test",
        )
    )

    assert models is None
    assert reason == "model_endpoint_not_configured"


def test_fallback_provider_key_requires_its_endpoint():
    models, reason = create_configured_models(
        Settings(
            openai_api_key="openai-key",
            openai_base_url="https://openai.example/v1",
            anthropic_api_key="anthropic-key",
            model_fast_name="fast-test",
            model_specialist_name="specialist-test",
        ),
        chat_model_factory=lambda **options: _FakeChatModel(**options),
    )

    assert models is None
    assert reason == "fallback_model_endpoint_not_configured"


def test_simulated_mode_builds_models_without_external_credentials():
    models, reason = create_configured_models(Settings(model_mode="simulated"))

    assert reason is None
    assert models is not None
    validate_model_capabilities(models)
    assert models[ModelPolicyId.FAST]._llm_type == "argos_simulated"


def test_simulated_model_emits_a_valid_route_plan():
    route = SimulatedChatModel().with_structured_output(RoutePlan).invoke("listar propriedades")

    assert route.tasks[0].agent is SpecialistAgentId.FARM


def test_simulated_model_preserves_router_request_for_document_search():
    from app.infrastructure.agents.simulated_model import _tool_arguments

    request = "Consulte os termos globais sintéticos e cite o trecho relevante."
    router_input = HumanMessage(content=json.dumps({"message": request, "session_context": {}}))

    plan = SimulatedChatModel._structured_call("RoutePlan", [router_input])[1]
    specialist_input = plan["tasks"][0]

    assert specialist_input["goal"] == request
    assert _tool_arguments("consultar_documental", specialist_input) == {"question": request}


def test_simulated_doctor_filters_catalog_by_requested_medicine_name():
    from app.infrastructure.agents.simulated_model import _tool_arguments

    request = "Consulte no catálogo o medicamento Ivervet Teste e informe o princípio ativo."

    assert _tool_arguments("list_medicines", {"goal": request}) == {
        "payload": {"brand_name": "Ivervet Teste"}
    }


def test_simulated_medicine_route_and_filter_support_vaccine_and_followup_fields():
    from app.infrastructure.agents.simulated_model import (
        _next_tool,
        _requested_medicine_name,
        _tool_arguments,
    )

    request = 'Qual o princípio ativo da "Vacina Teste A"?'
    route = SimulatedChatModel._structured_call(
        "RoutePlan", [HumanMessage(content=json.dumps({"message": request}))]
    )[1]
    assert route["tasks"][0]["agent"] == "doctor"
    assert _requested_medicine_name(request) == "Vacina Teste A"
    specialist = {"goal": request}
    assert _tool_arguments("list_medicines", specialist) == {
        "payload": {"brand_name": "Vacina Teste A"}
    }
    assert (
        _next_tool(
            ["list_medicines", "list_medicine_documents"],
            specialist,
            [
                HumanMessage(content=json.dumps(specialist)),
                ToolMessage(content='{"items": []}', tool_call_id="catalog", name="list_medicines"),
            ],
        )
        == "list_medicine_documents"
    )


def test_simulated_model_routes_report_through_farm_then_secretary():
    request = HumanMessage(
        content=json.dumps(
            {
                "message": "Envie o relatório do lote HC-LOTE-076 para usuario.teste@argos.local.",
            }
        )
    )
    route = RoutePlan.model_validate(SimulatedChatModel._structured_call("RoutePlan", [request])[1])

    assert [task.agent for task in route.tasks] == [
        SpecialistAgentId.FARM,
        SpecialistAgentId.SECRETARY,
    ]
    assert route.tasks[1].depends_on == (route.tasks[0].task_id,)
    assert route.tasks[0].goal.endswith("HC-LOTE-076.")
    assert route.tasks[0].required_evidence == ("signed_report_source",)
    assert route.tasks[1].required_evidence == ("confirmed_email_preparation",)


def test_simulated_router_does_not_infer_confirmation_from_free_text():
    route = (
        SimulatedChatModel()
        .with_structured_output(RoutePlan)
        .invoke("Confirmo a preparação. Envie agora o relatório já preparado.")
    )

    assert all(task.task_id != "simulated_secretary_send_prepared" for task in route.tasks)


def test_simulated_model_does_not_authorize_send_for_prepare_only_request():
    route = (
        SimulatedChatModel()
        .with_structured_output(RoutePlan)
        .invoke("Prepare o relatório do lote HC-LOTE-076 para usuario.teste@argos.local.")
    )

    assert "autorizou explicitamente o envio" not in route.tasks[1].goal


def test_simulated_model_requests_lot_code_when_report_request_omits_it():
    route = (
        SimulatedChatModel()
        .with_structured_output(RoutePlan)
        .invoke("Envie o relatório por e-mail, por favor.")
    )

    assert not route.tasks
    assert route.missing_slots == ("código do lote",)


def test_simulated_secretary_uses_report_artifact_from_farm_dependency():
    from app.infrastructure.agents.simulated_model import _tool_arguments

    reference = "ba0e1be8-62b1-4a48-ad62-17fa8f8a8048"
    messages = [
        HumanMessage(
            content=json.dumps(
                {
                    "task_id": "simulated_secretary_report",
                    "dependency_results": [
                        {"task_id": "simulated_farm_report", "report_artifact_refs": [reference]}
                    ],
                }
            )
        )
    ]

    payload = json.loads(messages[0].content)
    assert _tool_arguments("prepare_report", payload) == {
        "report_artifact_ref": reference,
        "report_title": "Relatório do lote solicitado",
    }


def test_simulated_secretary_sends_only_after_preparation_and_explicit_intent():
    from app.infrastructure.agents.simulated_model import _next_tool, _tool_arguments

    message = HumanMessage(
        content=json.dumps(
            {
                "task_id": "simulated_secretary_report",
                "goal": "Preparar e enviar; o pedido original autorizou explicitamente o envio.",
                "email_send_authorized": True,
            }
        )
    )
    tools = ["prepare_report", "send_report", "get_report_status"]
    ready = ToolMessage(
        content=json.dumps({"status": "prepared"}),
        tool_call_id="prepare-1",
        name="prepare_report",
    )
    unresolved = ToolMessage(
        content=json.dumps({"status": "needs_recipients"}),
        tool_call_id="prepare-2",
        name="prepare_report",
    )
    sent = ToolMessage(
        content=json.dumps({"status": "accepted", "report_id": "report-1"}),
        tool_call_id="send-1",
        name="send_report",
    )

    assert _next_tool(tools, json.loads(message.content), [message, ready]) == "send_report"
    assert _next_tool(tools, json.loads(message.content), [message, unresolved]) is None
    assert _next_tool(tools, json.loads(message.content), [message, sent]) is None
    assert _next_tool(tools, json.loads(message.content), []) == "prepare_report"
    confirmation = {
        "task_id": "simulated_task",
        "goal": "Enviar a preparação válida; o pedido original autorizou explicitamente o envio.",
        "email_send_authorized": False,
    }
    assert _next_tool(tools, confirmation, []) == "prepare_report"
    assert (
        _next_tool(
            ["resolve_report_lot_report_source", "list_lots"],
            {"task_id": "simulated_farm_report"},
            [],
        )
        == "resolve_report_lot_report_source"
    )
    assert _tool_arguments("send_report") == {}
    assert (
        _next_tool(
            tools,
            {"task_id": "simulated_secretary_report", "goal": "Preparar o relatório."},
            [message, ready],
        )
        is None
    )


def test_simulated_never_dispatches_without_a_preparation_result_in_this_execution():
    message = HumanMessage(
        content=json.dumps(
            {
                "task_id": "simulated_task",
                "goal": (
                    "Enviar a preparação válida; o pedido original autorizou explicitamente "
                    "o envio."
                ),
            }
        )
    )
    result = SimulatedChatModel()._generate(
        [message],
        tools=[{"name": "SpecialistResult"}, {"name": "send_report"}],
    )

    call = result.generations[0].message.tool_calls[0]
    assert call["name"] == "SpecialistResult"
    assert call["args"]["status"] == "unavailable"


def test_simulated_prepare_only_never_calls_dispatch_tool():
    message = HumanMessage(
        content=json.dumps(
            {
                "task_id": "simulated_secretary_report",
                "goal": "Preparar relatório sem enviar e-mail.",
            }
        )
    )
    result = SimulatedChatModel()._generate(
        [message],
        tools=[
            {"name": "SpecialistResult"},
            {"name": "prepare_report"},
            {"name": "send_report"},
        ],
    )

    call = result.generations[0].message.tool_calls[0]
    assert call["name"] == "prepare_report"


def test_simulated_specialist_preserves_tool_failure_instead_of_reporting_success():
    result = SimulatedChatModel._structured_call(
        "SpecialistResult",
        [
            ToolMessage(
                content="falha de execução",
                tool_call_id="tool-call-1",
                name="resolve_report_lot_report_source",
                status="error",
            )
        ],
    )[1]

    assert result["status"] == "error"
    assert "falhou" in result["answer"]


def test_simulated_specialist_returns_sanitized_tool_result():
    result = SimulatedChatModel._structured_call(
        "SpecialistResult",
        [
            ToolMessage(
                content='{"lot_code":"HC-01","contact":"ana@example.com"}',
                tool_call_id="tool-call-1",
                name="list_lots",
            )
        ],
    )[1]

    assert result["status"] == "success"
    assert '"lot_code":"HC-01"' in result["answer"]
    assert "ana@example.com" in result["answer"]
    assert "[EMAIL]" not in result["answer"]


def test_simulated_specialist_combines_catalog_and_document_results():
    result = SimulatedChatModel._structured_call(
        "SpecialistResult",
        [
            ToolMessage(
                content='{"items":[{"brand_name":"Vacina Teste A"}]}',
                tool_call_id="catalog",
                name="list_medicines",
            ),
            ToolMessage(
                content='{"items":[{"active_ingredient":"Composto sintético"}]}',
                tool_call_id="document",
                name="list_medicine_documents",
            ),
        ],
    )[1]

    assert result["status"] == "success"
    assert "list_medicines" in result["answer"]
    assert "list_medicine_documents" in result["answer"]
    assert "Composto sintético" in result["answer"]


def test_simulated_orchestrator_does_not_claim_success_for_failed_specialist():
    result = SimulatedChatModel._structured_call(
        "OrchestratorResult",
        [HumanMessage(content=json.dumps({"outcomes": [{"result": {"status": "error"}}]}))],
    )[1]

    assert result["draft_answer"] == (
        "Não tenho informação suficiente para responder com segurança agora."
    )


def test_simulated_orchestrator_includes_verified_specialist_results():
    result = SimulatedChatModel._structured_call(
        "OrchestratorResult",
        [
            HumanMessage(
                content=json.dumps(
                    {
                        "outcomes": [
                            {
                                "result": {
                                    "status": "success",
                                    "answer": (
                                        'Resultado retornado por list_lots: [{"lot":"HC-01"}]'
                                    ),
                                    "sources": [
                                        {
                                            "title": "Consulta de lote",
                                            "reference": "mcp://farm/lots/HC-01",
                                            "excerpt": "Lote HC-01",
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                )
            )
        ],
    )[1]

    assert result["draft_answer"] == ('Resultado retornado por list_lots: [{"lot":"HC-01"}]')
    assert result["sources"] == [
        {
            "title": "Consulta de lote",
            "reference": "mcp://farm/lots/HC-01",
            "excerpt": "Lote HC-01",
        }
    ]


def test_simulated_prediction_tool_arguments_match_mcp_contract():
    from app.infrastructure.agents.simulated_model import _tool_arguments

    payload = _tool_arguments("estimate_medicine_consumption")["payload"]
    request = EstimateMedicineConsumptionRequest.model_validate(payload)

    assert request.horizon_days == 7


def test_factory_builds_fast_and_specialist_models_only():
    calls = []

    def factory(**options):
        calls.append(options)
        return _FakeChatModel(**options)

    models, reason = create_configured_models(
        Settings(
            openai_api_key="test-secret",
            openai_base_url="https://openai.example/v1",
            model_fast_name="fast-test",
            model_specialist_name="specialist-test",
        ),
        chat_model_factory=factory,
    )

    assert reason is None
    assert models is not None
    assert len(calls) == 2
    assert models[ModelPolicyId.FAST].model == "fast-test"
    assert models[ModelPolicyId.SPECIALIST].model == "specialist-test"
    assert models[ModelPolicyId.FAST] is not models[ModelPolicyId.SPECIALIST]
    assert calls[0]["api_key"] == "test-secret"
    assert all(call["max_retries"] == 2 for call in calls)
    assert all(call["max_tokens"] == 4096 for call in calls)
    assert all(call["reasoning_effort"] == "none" for call in calls)
    assert all(call["base_url"] == "https://openai.example/v1" for call in calls)
    assert all("extra_body" not in call for call in calls)


def test_factory_uses_configured_openai_base_url():
    calls = []

    def factory(**options):
        calls.append(options)
        return _FakeChatModel(**options)

    create_configured_models(
        Settings(
            openai_api_key="test-secret",
            openai_base_url="https://openai-gateway.example/v1",
            model_fast_name="fast-test",
            model_specialist_name="specialist-test",
        ),
        chat_model_factory=factory,
    )

    assert all(call["base_url"] == "https://openai-gateway.example/v1" for call in calls)


def test_factory_uses_configured_anthropic_base_url(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class _FallbackFakeChatModel(_FakeChatModel):
        def bind_tools(self, _tools, **_options):
            return self

    def fallback_factory(**options):
        calls.append(options)
        return _FallbackFakeChatModel(**options)

    monkeypatch.setitem(
        sys.modules,
        "langchain_anthropic",
        SimpleNamespace(ChatAnthropic=fallback_factory),
    )
    settings = Settings(
        openai_api_key="openai-key",
        anthropic_api_key="anthropic-key",
        anthropic_base_url="https://anthropic-gateway.example",
        model_fast_fallback_name="fallback-fast-test",
        model_specialist_fallback_name="fallback-specialist-test",
        model_fast_name="fast-test",
        model_specialist_name="specialist-test",
    )
    primary_models = {policy: _FakeChatModel(model=policy.value) for policy in ModelPolicyId}
    _attach_anthropic_fallbacks(primary_models, settings)

    assert len(calls) == 2
    assert all(call["base_url"] == "https://anthropic-gateway.example" for call in calls)


def test_provider_base_urls_are_validated():
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        Settings(openai_base_url="file:///tmp/provider")

    with pytest.raises(ValueError, match="ANTHROPIC_BASE_URL"):
        Settings(anthropic_base_url="https://user:password@provider.example")


def test_factory_reuses_instance_when_both_roles_share_a_model_name():
    calls = []

    def factory(**options):
        calls.append(options)
        return _FakeChatModel(**options)

    models, reason = create_configured_models(
        Settings(
            openai_api_key="test-secret",
            openai_base_url="https://openai.example/v1",
            model_fast_name="shared-test",
            model_specialist_name="shared-test",
        ),
        chat_model_factory=factory,
    )

    assert reason is None
    assert models is not None
    assert models[ModelPolicyId.FAST] is models[ModelPolicyId.SPECIALIST]
    assert len(calls) == 1


def test_fast_and_specialist_model_names_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_FAST", "fast-env")
    monkeypatch.setenv("OPENAI_MODEL_SPECIALIST", "specialist-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-gateway.example/v1/")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic-gateway.example/")
    monkeypatch.setenv("ANTHROPIC_MODEL_FAST_FALLBACK", "haiku-env")
    monkeypatch.setenv("ANTHROPIC_MODEL_SPECIALIST_FALLBACK", "sonnet-env")

    settings = Settings.from_env()

    assert settings.model_fast_name == "fast-env"
    assert settings.model_specialist_name == "specialist-env"
    assert settings.anthropic_api_key == "anthropic-test-key"
    assert settings.openai_base_url == "https://openai-gateway.example/v1"
    assert settings.anthropic_base_url == "https://anthropic-gateway.example"
    assert settings.model_fast_fallback_name == "haiku-env"
    assert settings.model_specialist_fallback_name == "sonnet-env"


def test_provider_urls_are_not_defaulted_from_environment(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    settings = Settings.from_env()

    assert settings.openai_base_url is None
    assert settings.anthropic_base_url is None


def test_fallback_models_have_no_built_in_names(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL_FAST_FALLBACK", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL_SPECIALIST_FALLBACK", raising=False)

    settings = Settings.from_env()

    assert settings.model_fast_fallback_name is None
    assert settings.model_specialist_fallback_name is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_max_output_tokens": 0},
        {"model_max_output_tokens": 8193},
        {"model_max_retries": -1},
        {"model_max_retries": 3},
        {"document_a2a_timeout_seconds": 0},
        {"document_a2a_timeout_seconds": 61},
        {"execution_max_model_calls": 0},
        {"execution_max_tool_calls": 101},
        {"execution_timeout_seconds": 301},
    ],
)
def test_execution_limits_are_validated(overrides):
    with pytest.raises(ValueError):
        replace(Settings(), **overrides)


def test_execution_limits_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("MAIN_API_MODEL_MAX_OUTPUT_TOKENS", "900")
    monkeypatch.setenv("MAIN_API_MODEL_MAX_RETRIES", "2")
    monkeypatch.setenv("MAIN_API_EXECUTION_MAX_MODEL_CALLS", "8")
    monkeypatch.setenv("MAIN_API_EXECUTION_MAX_TOOL_CALLS", "7")
    monkeypatch.setenv("MAIN_API_EXECUTION_TIMEOUT_SECONDS", "45")

    settings = Settings.from_env()

    assert settings.model_max_output_tokens == 900
    assert settings.model_max_retries == 2
    assert settings.execution_max_model_calls == 8
    assert settings.execution_max_tool_calls == 7
    assert settings.execution_timeout_seconds == 45


def test_document_a2a_timeout_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("MAIN_API_DOCUMENT_A2A_TIMEOUT_SECONDS", "50")

    assert Settings.from_env().document_a2a_timeout_seconds == 50


def test_model_factory_rejects_model_without_native_structured_output():
    class NoToolBinding:
        pass

    models = {
        ModelPolicyId.FAST: NoToolBinding(),
        ModelPolicyId.SPECIALIST: _FakeChatModel(model="specialist"),
    }
    with pytest.raises(ModelRuntimeConfigurationError, match="saída estruturada nativa"):
        validate_model_capabilities(models)


def test_model_factory_rejects_specialist_without_tool_support():
    class NoToolBinding:
        def with_structured_output(self, _schema):
            return self

    models = {policy: _FakeChatModel(model=policy.value) for policy in ModelPolicyId}
    models[ModelPolicyId.SPECIALIST] = NoToolBinding()
    with pytest.raises(ModelRuntimeConfigurationError, match="vincular ferramentas"):
        validate_model_capabilities(models)


def test_model_factory_does_not_require_tools_for_fast_agents():
    class NativeStructuredOnly:
        def with_structured_output(self, _schema):
            return self

    models = {
        ModelPolicyId.FAST: NativeStructuredOnly(),
        ModelPolicyId.SPECIALIST: _FakeChatModel(model="specialist"),
    }
    validate_model_capabilities(models)
