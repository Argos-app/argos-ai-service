import sys
from types import ModuleType

import pytest
from argos_mcp_email.server import (
    ServiceAuthenticationError,
    ServiceAuthenticator,
    register_email_tools,
)
from argos_mcp_email.settings import Settings


def test_authenticator_requires_expected_bearer_token() -> None:
    authenticator = ServiceAuthenticator("secret")
    authenticator.verify("Bearer secret")

    with pytest.raises(ServiceAuthenticationError):
        authenticator.verify("Bearer wrong")


def test_settings_reads_service_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_EMAIL_INTERNAL_SERVICE_TOKEN", "secret")

    assert Settings.from_env().internal_service_token == "secret"


def test_register_email_tools_registers_operations_on_given_server(monkeypatch) -> None:
    class ToolRegistry:
        def __init__(self) -> None:
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function

            return register

    server = ToolRegistry()
    fastmcp_module = ModuleType("mcp.server.fastmcp")
    fastmcp_module.Context = type("Context", (), {})
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)
    register_email_tools(server, object())

    assert set(server.tools) == {"prepare_report", "send_report", "get_report_status"}
