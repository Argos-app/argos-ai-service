"""Testes locais da integração LangSmith sem envio de traces externos."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest
from app.infrastructure.observability import langsmith as langsmith_module
from app.settings import Settings


def test_langsmith_is_disabled_without_explicit_api_key():
    assert langsmith_module.create_langsmith_tracing(Settings()) is None


def test_installed_langchain_tracer_accepts_agent_address():
    from langchain_core.tracers import LangChainTracer
    from langsmith import agent_address

    expected_address = agent_address("test-agent", "local")
    callback = LangChainTracer(client=object(), address=expected_address)

    assert callback.address == expected_address


def test_langsmith_client_hides_content_and_anonymizes_non_email_sensitive_values(monkeypatch):
    client_arguments = {}

    class FakeClient:
        def __init__(self, **kwargs):
            client_arguments.update(kwargs)

        def close(self):
            pass

    class FakeTracer:
        def __init__(self, **kwargs):
            self.arguments = kwargs

    monkeypatch.setattr("langsmith.Client", FakeClient)
    monkeypatch.setattr("langchain_core.tracers.LangChainTracer", FakeTracer)

    tracing = langsmith_module.create_langsmith_tracing(
        Settings(
            langsmith_api_key="test-key",
            langsmith_agent_id="test-agent",
            langsmith_agent_environment="local",
            langsmith_workspace_id="workspace-id",
        )
    )

    assert tracing is not None
    assert client_arguments["api_key"] == "test-key"
    assert client_arguments["workspace_id"] == "workspace-id"
    assert client_arguments["hide_inputs"] is True
    assert client_arguments["hide_outputs"] is True
    expected_address = import_module("langsmith.address").agent_address("test-agent", "local")
    assert tracing.callback.arguments["address"] == expected_address
    assert "project_name" not in tracing.callback.arguments
    anonymized = client_arguments["anonymizer"](
        {
            "input": "Bearer token-value user@example.com 123.456.789-00",
            "id": "123e4567-e89b-12d3-a456-426614174000",
        }
    )
    assert "token-value" not in str(anonymized)
    assert "user@example.com" in str(anonymized)
    assert "123.456.789-00" not in str(anonymized)
    assert "123e4567-e89b-12d3-a456-426614174000" not in str(anonymized)

    correlation_hashes = [
        "d10f29cd8648fa1b04471ff487187bf6f1143626180dccc2f927c5f739362814",
        "ad3922d929413df2676254251a147bb98d8fea87c674593cec85f3a21b18adc6",
        "8c1d8de6a303ae7ae5a583e73227f30c59d3070281372beb1db425db12343a80",
    ]
    protected = client_arguments["anonymizer"](
        {"keys": correlation_hashes[:], "phone": "+55 11 98765-4321"}
    )
    assert protected["keys"] == correlation_hashes
    assert "98765-4321" not in protected["phone"]


def test_langsmith_closes_client_when_tracer_setup_fails(monkeypatch, caplog):
    state = SimpleNamespace(closed=False)

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def close(self):
            state.closed = True

    def fail_tracer(**kwargs):
        raise RuntimeError("secret-value")

    monkeypatch.setattr("langsmith.Client", FakeClient)
    monkeypatch.setattr("langchain_core.tracers.LangChainTracer", fail_tracer)

    assert langsmith_module.create_langsmith_tracing(Settings(langsmith_api_key="test-key")) is None
    assert state.closed
    assert "secret-value" not in caplog.text
    assert "test-key" not in caplog.text


def test_capture_content_requires_explicit_api_key():
    with pytest.raises(ValueError, match="CAPTURE_CONTENT exige"):
        Settings(langsmith_capture_content=True)


def test_langsmith_workspace_id_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("MAIN_API_LANGSMITH_WORKSPACE_ID", "workspace-id")

    assert Settings.from_env().langsmith_workspace_id == "workspace-id"


@pytest.mark.parametrize(
    ("app_env", "expected"),
    [
        ("local", "local"),
        ("development", "development"),
        ("staging", "staging"),
        ("production", "production"),
    ],
)
def test_langsmith_agent_environment_follows_app_environment(monkeypatch, app_env, expected):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("MAIN_API_LANGSMITH_AGENT_ENVIRONMENT", raising=False)

    assert Settings.from_env().langsmith_agent_environment == expected


def test_langsmith_run_includes_provider_billing_as_trace_metadata():
    run = _fake_run({"generations": [{"response_metadata": {"usage": {"cost_usd": "0.01"}}}]})

    langsmith_module._attach_provider_billing(run)

    assert run.extra["metadata"]["existing"] == "preserved"
    assert run.extra["metadata"]["argos_billing"] == {
        "reported_cost": "0.01",
        "currency": "USD",
        "cost_source": "provider",
    }
    assert run.extra["metadata"]["usage_metadata"]["total_cost"] == 0.01
    assert run.outputs["usage_metadata"]["total_cost"] == 0.01


def test_langsmith_run_includes_configured_rate_estimate(monkeypatch):
    monkeypatch.setenv("MODEL_FAST_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    monkeypatch.setenv("MODEL_FAST_OUTPUT_COST_PER_MILLION_TOKENS", "10")
    run = _fake_run(
        {
            "generations": [
                [
                    {
                        "message": {
                            "kwargs": {
                                "usage_metadata": {
                                    "input_tokens": 200,
                                    "output_tokens": 50,
                                    "total_tokens": 250,
                                }
                            }
                        }
                    }
                ]
            ]
        }
    )

    langsmith_module._attach_provider_billing(run, rate_profile="fast")

    assert run.extra["metadata"]["argos_billing"] == {
        "reported_cost": "0.0010",
        "currency": "USD",
        "cost_source": "configured_rate",
    }
    assert run.extra["metadata"]["usage_metadata"]["total_cost"] == 0.001
    assert run.extra["metadata"]["usage_metadata"]["input_cost"] == 0.0005
    assert run.extra["metadata"]["usage_metadata"]["output_cost"] == 0.0005
    assert run.outputs["usage_metadata"] == run.extra["metadata"]["usage_metadata"]


def test_langchain_tracer_persists_cost_with_token_usage_in_one_terminal_patch(monkeypatch):
    monkeypatch.setenv("MODEL_FAST_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    monkeypatch.setenv("MODEL_FAST_OUTPUT_COST_PER_MILLION_TOKENS", "10")
    monkeypatch.setenv("OPENAI_MODEL_FAST", "gpt-6-luna")
    patches = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    class FakeTracer:
        def __init__(self, **kwargs):
            pass

        @staticmethod
        def _update_run_single(run):
            patches.append(
                {
                    "metadata": dict(run.extra["metadata"]),
                    "usage_metadata": dict(run.extra["metadata"]["usage_metadata"]),
                    "output_usage_metadata": dict(run.outputs["usage_metadata"]),
                }
            )

        def _on_llm_end(self, run):
            self._update_run_single(run)

    monkeypatch.setattr("langsmith.Client", FakeClient)
    monkeypatch.setattr("langchain_core.tracers.LangChainTracer", FakeTracer)
    tracing = langsmith_module.create_langsmith_tracing(Settings(langsmith_api_key="test-key"))
    run = SimpleNamespace(
        outputs={
            "generations": [
                [
                    {
                        "message": {
                            "kwargs": {
                                "usage_metadata": {
                                    "input_tokens": 200,
                                    "output_tokens": 50,
                                    "total_tokens": 250,
                                }
                            }
                        }
                    }
                ]
            ]
        },
        extra={
            "metadata": {
                "existing": "preserved",
                "ls_provider": "openai",
                "ls_model_name": "gpt-6-luna",
            }
        },
        set=lambda *, metadata=None, usage_metadata=None: _set_run_metadata(
            run, metadata=metadata, usage_metadata=usage_metadata
        ),
    )

    tracing.callback._on_llm_end(run)

    assert len(patches) == 1
    assert patches[0]["metadata"]["existing"] == "preserved"
    assert patches[0]["metadata"]["argos_billing"] == {
        "reported_cost": "0.0010",
        "currency": "USD",
        "cost_source": "configured_rate",
    }
    assert patches[0]["usage_metadata"] == {
        "input_tokens": 200,
        "output_tokens": 50,
        "total_tokens": 250,
        "total_cost": 0.001,
        "input_cost": 0.0005,
        "output_cost": 0.0005,
    }
    assert patches[0]["output_usage_metadata"] == patches[0]["usage_metadata"]


def _set_run_metadata(run, *, metadata=None, usage_metadata=None):
    if metadata is not None:
        run.extra.setdefault("metadata", {}).update(metadata)
    if usage_metadata is not None:
        run.extra.setdefault("metadata", {})["usage_metadata"] = usage_metadata


def _fake_run(outputs):
    run = SimpleNamespace(outputs=outputs, extra={"metadata": {"existing": "preserved"}})
    run.set = lambda *, metadata=None, usage_metadata=None: _set_run_metadata(
        run, metadata=metadata, usage_metadata=usage_metadata
    )
    return run
