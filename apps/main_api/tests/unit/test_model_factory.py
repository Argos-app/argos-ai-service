"""Configuração e capacidades do runtime de modelos do principal."""

from dataclasses import replace

import pytest
from app.agent_schemas.results import RoutePlan, SpecialistAgentId
from app.agents.registry import ModelPolicyId
from app.infrastructure.agents.model_factory import (
    ModelRuntimeConfigurationError,
    create_configured_models,
    validate_model_capabilities,
)
from app.infrastructure.agents.simulated_model import SimulatedChatModel
from app.settings import Settings


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
        Settings(model_fast_name="fast-test", model_specialist_name="specialist-test")
    )

    assert models is None
    assert reason == "model_credentials_not_configured"


def test_partial_model_configuration_is_reported_without_starting_provider():
    models, reason = create_configured_models(Settings(model_fast_name="fast-test"))

    assert models is None
    assert reason == "model_configuration_incomplete"


def test_simulated_mode_builds_models_without_external_credentials():
    models, reason = create_configured_models(Settings(model_mode="simulated"))

    assert reason is None
    assert models is not None
    validate_model_capabilities(models)
    assert models[ModelPolicyId.ROUTER]._llm_type == "argos_simulated"


def test_simulated_model_emits_a_valid_route_plan():
    route = SimulatedChatModel().with_structured_output(RoutePlan).invoke("listar propriedades")

    assert route.tasks[0].agent is SpecialistAgentId.FARM


def test_factory_maps_internal_policies_to_fast_and_specialist_models():
    calls = []

    def factory(**options):
        calls.append(options)
        return _FakeChatModel(**options)

    models, reason = create_configured_models(
        Settings(
            model_api_base_url="https://models.example/v1",
            model_api_key="test-secret",
            model_fast_name="fast-test",
            model_specialist_name="specialist-test",
        ),
        chat_model_factory=factory,
    )

    assert reason is None
    assert models is not None
    assert calls[0]["model"] == "fast-test"
    assert calls[1]["model"] == "specialist-test"
    assert models[ModelPolicyId.ROUTER] is models[ModelPolicyId.JUDGE]
    assert models[ModelPolicyId.FARM] is models[ModelPolicyId.ANALYST]
    assert models[ModelPolicyId.ROUTER] is not models[ModelPolicyId.FARM]
    assert calls[0]["api_key"] == "test-secret"
    assert calls[0]["max_retries"] == 0
    assert calls[0]["max_tokens"] == 1200


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_max_output_tokens": 0},
        {"model_max_output_tokens": 8193},
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
    monkeypatch.setenv("MAIN_API_EXECUTION_MAX_MODEL_CALLS", "8")
    monkeypatch.setenv("MAIN_API_EXECUTION_MAX_TOOL_CALLS", "7")
    monkeypatch.setenv("MAIN_API_EXECUTION_TIMEOUT_SECONDS", "45")

    settings = Settings.from_env()

    assert settings.model_max_output_tokens == 900
    assert settings.execution_max_model_calls == 8
    assert settings.execution_max_tool_calls == 7
    assert settings.execution_timeout_seconds == 45


def test_model_factory_rejects_models_without_structured_output():
    class NoStructuredOutput:
        def bind_tools(self, _tools):
            return self

    models = {policy: NoStructuredOutput() for policy in ModelPolicyId}
    with pytest.raises(ModelRuntimeConfigurationError, match="saída estruturada"):
        validate_model_capabilities(models)


def test_model_factory_rejects_specialist_without_tool_support():
    class NoToolBinding:
        def with_structured_output(self, _schema):
            return self

    models = {policy: _FakeChatModel(model=policy.value) for policy in ModelPolicyId}
    models[ModelPolicyId.FARM] = NoToolBinding()
    with pytest.raises(ModelRuntimeConfigurationError, match="chamada de ferramentas"):
        validate_model_capabilities(models)
