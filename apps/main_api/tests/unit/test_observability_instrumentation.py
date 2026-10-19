"""Verifica instrumentação best-effort nas fronteiras da API, tools e MCP."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.application.email_workflow import EmailWorkflow
from app.infrastructure.mcp.client import MCP_TOOL_ALLOWLISTS, McpToolServerClient
from app.infrastructure.observability.langchain_callbacks import ObservabilityToolCallback
from app.infrastructure.observability.recording import OperationRecorder
from app.main import create_app
from app.settings import Settings

from packages.observability import (
    BestEffortObservationCollector,
    ErrorOrigin,
    ExecutionMode,
    ObservationLevel,
    ObservationStatus,
    OperationKind,
    bind_correlation,
)
from packages.observability.testing import InMemoryObservationWriter


def _collector():
    writer = InMemoryObservationWriter()
    return BestEffortObservationCollector(writer), writer


def _run_immediate(coroutine):
    iterator = coroutine.__await__()
    while True:
        try:
            yielded = iterator.send(None)
        except StopIteration as completed:
            return completed.value
        if yielded is not None:
            raise AssertionError("fake de unidade não deveria suspender a corrotina")


def test_openapi_documents_correlation_header_on_versioned_routes():
    schema = create_app(Settings()).openapi()

    for path, path_item in schema["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            assert operation["responses"]
            assert all(
                "X-Argos-Correlation-ID" in response.get("headers", {})
                for response in operation["responses"].values()
            )


def test_recorder_uses_sanitized_error_catalog_and_emits_terminal_event():
    collector, writer = _collector()
    recorder = OperationRecorder(
        collector,
        operation_kind=OperationKind.TOOL,
        operation_name="tool.private_operation",
        error_origin=ErrorOrigin.APPLICATION,
    )

    recorder.finish(error=RuntimeError("private customer data and secret value"))

    started, failed = writer.observations
    assert started.status == ObservationStatus.STARTED
    assert failed.status == ObservationStatus.FAILED
    assert failed.duration_ms is not None
    assert failed.error is not None
    assert failed.error.code == "operation_failed"
    assert failed.error.message_sanitized == "operation failed"
    assert "private customer data" not in repr(failed)


@pytest.mark.asyncio
async def test_tool_callback_marks_mock_and_unavailable_without_storing_content():
    collector, writer = _collector()
    callback = ObservabilityToolCallback(collector, "fazenda")
    mock_run_id = uuid4()
    await callback.on_tool_start(
        {"name": "mock_consultar_fazenda"},
        '{"api_key":"do-not-store"}',
        run_id=mock_run_id,
    )
    await callback.on_tool_end("private tool output", run_id=mock_run_id)

    unavailable_run_id = uuid4()
    await callback.on_tool_start(
        {"name": "mcp_data_unavailable"},
        "private arguments",
        run_id=unavailable_run_id,
    )
    await callback.on_tool_end("unavailable", run_id=unavailable_run_id)

    assert [item.execution_mode for item in writer.observations] == [
        ExecutionMode.MOCK,
        ExecutionMode.MOCK,
        ExecutionMode.UNAVAILABLE,
        ExecutionMode.UNAVAILABLE,
    ]
    assert writer.observations[1].status == ObservationStatus.SUCCEEDED
    assert writer.observations[3].status == ObservationStatus.REJECTED
    assert writer.observations[3].result.code == "dependency_unavailable"
    assert "do-not-store" not in repr(writer.observations)
    assert "private tool output" not in repr(writer.observations)


def test_operation_recorder_inherits_correlation_and_parent_observation():
    collector, writer = _collector()
    correlation_id = uuid4()
    parent_observation_id = uuid4()
    session_id = uuid4()
    interaction_id = uuid4()

    from packages.observability import bind_interaction

    with bind_correlation(correlation_id), bind_interaction(session_id, interaction_id):
        from packages.observability import observation_scope

        with observation_scope(parent_observation_id):
            recorder = OperationRecorder(
                collector,
                operation_kind=OperationKind.MCP,
                operation_name="mcp.data.call_tool.list_lots",
            )
            recorder.finish()

    assert writer.observations[0].correlation_id == correlation_id
    assert writer.observations[0].parent_observation_id == parent_observation_id
    assert writer.observations[0].session_id == session_id
    assert writer.observations[0].interaction_id == interaction_id


def test_mcp_tool_call_is_observed_without_arguments_or_response(monkeypatch):
    collector, writer = _collector()
    correlation_id = uuid4()
    captured_headers = {}

    class FakeSession:
        async def call_tool(self, _name, _arguments):
            return SimpleNamespace(
                isError=False,
                model_dump=lambda **_kwargs: {"content": "private response"},
            )

    @asynccontextmanager
    async def observed_fake_session(self, request_headers=None):
        del self
        captured_headers.update(request_headers or {})
        yield FakeSession()

    monkeypatch.setattr(McpToolServerClient, "_session", observed_fake_session)
    client = McpToolServerClient(
        service_name="data",
        url="http://127.0.0.1:8000/mcp",
        service_token="test-token",
        allowed_tools=MCP_TOOL_ALLOWLISTS["data"],
        observation_collector=collector,
    )

    with bind_correlation(correlation_id):
        result = _run_immediate(
            client.call_tool("list_lots", {"payload": {"secret": "private input"}})
        )

    assert result.isError is False
    start, terminal = writer.observations
    assert start.operation_name == "mcp.data.call_tool.list_lots"
    assert start.observation_level == ObservationLevel.SDK_INVOCATION
    assert terminal.status == ObservationStatus.SUCCEEDED
    assert terminal.duration_ms is not None
    assert captured_headers["X-Argos-Correlation-ID"] == str(correlation_id)
    assert "private input" not in repr(writer.observations)
    assert "private response" not in repr(writer.observations)


@pytest.mark.asyncio
async def test_email_workflows_record_failure_and_recovery_without_payloads():
    collector, writer = _collector()
    email_client = SimpleNamespace(service_name="email")
    preparations = object()
    artifacts = object()
    preparation = EmailWorkflow(
        email_client=email_client,
        report_artifacts=artifacts,
        preparations=preparations,
        observation_collector=collector,
    )
    dispatch = EmailDispatchWorkflow(
        email_client=email_client,
        preparations=preparations,
        report_artifacts=artifacts,
        observation_collector=collector,
    )

    with pytest.raises(ValueError):
        await preparation.prepare(
            report_artifact_ref=uuid4(), report_title="relatório", configurable={}
        )
    with pytest.raises(ValueError):
        await dispatch.send(configurable={})
    recovered = await dispatch.recover_interaction_outcome(configurable={})

    operations = [item.operation_name for item in writer.observations]
    assert operations == [
        "email.prepare",
        "email.prepare",
        "email.dispatch.send",
        "email.dispatch.send",
        "email.dispatch.recovery",
        "email.dispatch.recovery",
    ]
    assert writer.observations[1].status == ObservationStatus.FAILED
    assert writer.observations[3].status == ObservationStatus.FAILED
    assert writer.observations[4].operation_phase.value == "recovery"
    assert writer.observations[5].status == ObservationStatus.SUCCEEDED
    assert writer.observations[5].result.code == "recovery_result"
    assert writer.observations[5].result.recovered is True
    assert "MCP não confirmou" in recovered
