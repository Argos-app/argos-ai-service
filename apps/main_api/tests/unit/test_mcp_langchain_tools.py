"""Bindings MCP para os especialistas LangChain."""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from app.agents.tools.email import build_email_send_tool, build_email_status_tool
from app.infrastructure.mcp.client import MCP_TOOL_ALLOWLISTS, McpClients
from app.infrastructure.mcp.langchain_tools import (
    McpToolBindingConfigurationError,
    build_mcp_tool_bindings,
)
from app.ports.agent import AgentId


class _FakeClient:
    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        self.allowed_tools = MCP_TOOL_ALLOWLISTS[service_name]
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, name, arguments, *, request_headers=None):
        self.calls.append(
            {"name": name, "arguments": arguments, "request_headers": request_headers}
        )
        return SimpleNamespace(
            structuredContent={"tool": name, "ok": True},
            content=[],
        )


class _FakeIssuer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def headers_for_call(self, **claims):
        self.calls.append(claims)
        return {"X-Argos-Delegation": "signed-per-call-token"}


def _tool(tools, name: str):
    return next(tool for tool in tools if tool.name == name)


@pytest.mark.asyncio
async def test_farm_tool_sends_delegation_for_fixed_agent_operation():
    data_client = _FakeClient("data")
    issuer = _FakeIssuer()
    bindings = build_mcp_tool_bindings(
        McpClients(data=data_client),
        issuer,
    )
    user_id, property_id, request_id = uuid4(), uuid4(), uuid4()

    response = await _tool(bindings[AgentId.FARM]["farm_data"], "list_properties").ainvoke(
        {"payload": {"property_ids": [str(property_id)]}},
        config={
            "configurable": {
                "argos_user_id": str(user_id),
                "argos_authorized_property_ids": (str(property_id),),
                "argos_request_id": str(request_id),
            }
        },
    )

    assert '"ok": true' in response
    assert issuer.calls == [
        {
            "service": "data",
            "user_id": str(user_id),
            "authorized_property_ids": (str(property_id),),
            "agent": "fazenda",
            "operation": "cattle.read",
            "request_id": str(request_id),
        }
    ]
    assert data_client.calls[0]["request_headers"] == {
        "X-Argos-Delegation": "signed-per-call-token"
    }


@pytest.mark.asyncio
async def test_doctor_tools_use_doctor_identity_and_medicine_operation():
    data_client = _FakeClient("data")
    issuer = _FakeIssuer()
    bindings = build_mcp_tool_bindings(McpClients(data=data_client), issuer)
    config = {
        "configurable": {
            "argos_user_id": str(uuid4()),
            "argos_authorized_property_ids": (str(uuid4()),),
            "argos_request_id": str(uuid4()),
        }
    }

    await _tool(bindings[AgentId.DOCTOR]["medicine_reference"], "list_medicines").ainvoke(
        {"payload": {}}, config=config
    )

    assert issuer.calls[0]["agent"] == "doutor"
    assert issuer.calls[0]["operation"] == "medicine.read"


@pytest.mark.asyncio
async def test_secretary_email_binding_exposes_only_local_report_workflow_tools():
    email_client = _FakeClient("email")
    bindings = build_mcp_tool_bindings(McpClients(email=email_client), None)
    tools = bindings[AgentId.SECRETARY]["report_delivery"]

    assert {tool.name for tool in tools} == {
        "prepare_report",
        "send_report",
        "get_report_status",
    }
    assert email_client.calls == []


@pytest.mark.asyncio
async def test_email_action_tools_take_identity_and_send_details_only_from_runtime():
    class _DispatchWorkflow:
        def __init__(self):
            self.send_config = None
            self.status_action_ref = "unset"
            self.status_config = None

        async def send(self, *, configurable):
            self.send_config = configurable
            return "send result"

        async def status(self, *, authorization_id, configurable):
            self.status_action_ref = authorization_id
            self.status_config = configurable
            return "status result"

    workflow = _DispatchWorkflow()
    send_tool = build_email_send_tool(workflow)
    status_tool = build_email_status_tool(workflow)
    config = {
        "configurable": {
            "argos_user_id": str(uuid4()),
            "session_id": str(uuid4()),
            "argos_send_intent_source_interaction_id": str(uuid4()),
        }
    }

    assert await send_tool.ainvoke({}, config=config) == "send result"
    assert await status_tool.ainvoke({}, config=config) == "status result"
    assert set(send_tool.args_schema.model_fields) == set()
    assert set(status_tool.args_schema.model_fields) == {"action_ref"}
    assert workflow.send_config == config["configurable"]
    assert workflow.status_action_ref is None
    assert workflow.status_config == config["configurable"]


def test_data_tools_require_delegation_signer():
    with pytest.raises(McpToolBindingConfigurationError, match="DELEGATION"):
        build_mcp_tool_bindings(McpClients(data=_FakeClient("data")), None)


def test_explicit_empty_mcp_configuration_uses_unavailable_tools_not_secretary_mock():
    bindings = build_mcp_tool_bindings(McpClients(), None)

    secretary_tools = bindings[AgentId.SECRETARY]["report_delivery"]
    assert [tool.name for tool in secretary_tools] == ["mcp_email_unavailable"]
    assert all(tool.name != "mock_preparar_relatorio" for tool in secretary_tools)


def test_configured_mcp_tools_are_bound_to_expected_agents():
    bindings = build_mcp_tool_bindings(
        McpClients(
            data=_FakeClient("data"),
            prediction=_FakeClient("prediction"),
            email=_FakeClient("email"),
        ),
        _FakeIssuer(),
    )

    assert "list_properties" in {tool.name for tool in bindings[AgentId.FARM]["farm_data"]}
    assert "resolve_report_lot_report_source" in {
        tool.name for tool in bindings[AgentId.FARM]["farm_data"]
    }
    assert {tool.name for tool in bindings[AgentId.DOCTOR]["medicine_reference"]} == {
        "list_medicines",
        "list_medicine_documents",
    }
    assert {tool.name for tool in bindings[AgentId.ANALYST]["prediction"]} == {
        "estimate_medicine_consumption"
    }
    secretary_tools = {tool.name for tool in bindings[AgentId.SECRETARY]["report_delivery"]}
    assert secretary_tools == {"prepare_report", "send_report", "get_report_status"}
    assert "farm_data" not in bindings[AgentId.SECRETARY]
