"""Testes locais da configuração e do contexto LangSmith no serviço A2A."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from argos_a2a_agent.adapters.observability import langsmith as langsmith_module
from argos_a2a_agent.settings import Settings

from packages.observability import correlation_key


def test_langsmith_is_disabled_without_explicit_api_key():
    assert langsmith_module.create_langsmith_tracing(Settings()) is None


def test_langsmith_context_enables_configured_client_and_project(monkeypatch):
    client_arguments = {}
    context_arguments = {}

    class FakeClient:
        def __init__(self, **kwargs):
            client_arguments.update(kwargs)

        def close(self):
            pass

    @contextmanager
    def fake_tracing_context(**kwargs):
        context_arguments.update(kwargs)
        yield

    monkeypatch.setattr("langsmith.Client", FakeClient)
    monkeypatch.setattr("langsmith.tracing_context", fake_tracing_context)

    tracing = langsmith_module.create_langsmith_tracing(
        Settings(
            langsmith_api_key="test-key",
            langsmith_project="a2a-test",
            langsmith_workspace_id="workspace-id",
        )
    )

    assert tracing is not None
    assert client_arguments["workspace_id"] == "workspace-id"
    assert client_arguments["hide_inputs"] is True
    assert client_arguments["hide_outputs"] is True
    trace_data = client_arguments["anonymizer"]({"input": "recipient@example.com"})
    assert trace_data["input"] == "recipient@example.com"
    hashes = [
        "d10f29cd8648fa1b04471ff487187bf6f1143626180dccc2f927c5f739362814",
        "ad3922d929413df2676254251a147bb98d8fea87c674593cec85f3a21b18adc6",
        "8c1d8de6a303ae7ae5a583e73227f30c59d3070281372beb1db425db12343a80",
    ]
    protected = client_arguments["anonymizer"]({"keys": hashes[:], "phone": "+55 11 98765-4321"})
    assert protected["keys"] == hashes
    assert "98765-4321" not in protected["phone"]
    with tracing.context("corr-123"):
        pass
    assert context_arguments == {
        "enabled": True,
        "client": tracing.client,
        "project_name": "a2a-test",
        "metadata": {
            "argos_correlation_id": "corr-123",
            "argos_correlation_key": correlation_key("corr-123"),
        },
    }
    tracing.close()


def test_langsmith_workspace_id_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("A2A_LANGSMITH_WORKSPACE_ID", "workspace-id")

    assert Settings.from_env().langsmith_workspace_id == "workspace-id"


def test_a2a_capture_content_requires_explicit_api_key():
    settings = Settings(
        qdrant_url="https://qdrant.example",
        qdrant_read_api_key="read-key",
        qdrant_write_api_key="write-key",
        qdrant_collection="documents",
        qdrant_vector_size=3,
        document_source_root=Path.cwd() / "documents",
        manifest_path=Path.cwd() / "manifest.json",
        internal_service_token="service-token",
        resource_server_url="https://resource.example",
        delegation_issuer="argos-test",
        trusted_delegation_keys=(("key-id", "public-key"),),
        a2a_endpoint_url="https://a2a.example/a2a",
        permission_sql_dsn="postgresql://reader:password@database/documents",
        langsmith_capture_content=True,
    )
    try:
        settings.validate_runtime(require_providers=False)
    except ValueError as exc:
        assert "CAPTURE_CONTENT exige" in str(exc)
    else:
        raise AssertionError("configuração de captura sem chave deveria ser rejeitada")
