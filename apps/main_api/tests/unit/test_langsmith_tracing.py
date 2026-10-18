"""Testes locais da integração LangSmith sem envio de traces externos."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.infrastructure.observability import langsmith as langsmith_module
from app.settings import Settings


def test_langsmith_is_disabled_without_explicit_api_key():
    assert langsmith_module.create_langsmith_tracing(Settings()) is None


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
            langsmith_project="test-project",
            langsmith_workspace_id="workspace-id",
        )
    )

    assert tracing is not None
    assert client_arguments["api_key"] == "test-key"
    assert client_arguments["workspace_id"] == "workspace-id"
    assert client_arguments["hide_inputs"] is True
    assert client_arguments["hide_outputs"] is True
    assert tracing.callback.arguments["project_name"] == "test-project"
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
