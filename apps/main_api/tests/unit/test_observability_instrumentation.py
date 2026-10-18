"""Verifica instrumentação best-effort nas fronteiras da API, tools e MCP."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
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
)
from packages.observability.testing import InMemoryObservationWriter


def _collector():
    writer = InMemoryObservationWriter()
    return BestEffortObservationCollector(writer), writer


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


@pytest.mark.asyncio
async def test_http_boundary_records_route_template_status_and_duration():
    collector, writer = _collector()
    app = create_app(Settings())
    app.state.observability_collector = collector

    @app.get("/observability-test/{record_id}")
    async def endpoint(record_id: str):
        return {"record_id": record_id}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/observability-test/sensitive-id")

    assert response.status_code == 200
    terminal = writer.observations[-1]
    assert terminal.operation_kind == OperationKind.HTTP
    assert terminal.http.route == "/observability-test/{record_id}"
    assert terminal.http.status_code == 200
    assert terminal.duration_ms is not None
    assert "sensitive-id" not in repr(terminal)


@pytest.mark.asyncio
async def test_mcp_tool_call_is_observed_without_arguments_or_response(monkeypatch):
    collector, writer = _collector()

    class FakeSession:
        async def call_tool(self, _name, _arguments):
            return SimpleNamespace(
                isError=False,
                model_dump=lambda **_kwargs: {"content": "private response"},
            )

    @asynccontextmanager
    async def observed_fake_session(self, request_headers=None):
        del self, request_headers
        yield FakeSession()

    monkeypatch.setattr(McpToolServerClient, "_session", observed_fake_session)
    client = McpToolServerClient(
        service_name="data",
        url="http://127.0.0.1:8000/mcp",
        service_token="test-token",
        allowed_tools=MCP_TOOL_ALLOWLISTS["data"],
        observation_collector=collector,
    )

    result = await client.call_tool("list_lots", {"payload": {"secret": "private input"}})

    assert result.isError is False
    start, terminal = writer.observations
    assert start.operation_name == "mcp.data.call_tool.list_lots"
    assert start.observation_level == ObservationLevel.SDK_INVOCATION
    assert terminal.status == ObservationStatus.SUCCEEDED
    assert terminal.duration_ms is not None
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
