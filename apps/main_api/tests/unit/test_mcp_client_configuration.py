"""O HTTP interno do Compose só é permitido em configuração local explícita."""

import pytest
from app.infrastructure.mcp.client import McpClientConfigurationError, McpToolServerClient


def test_compose_service_host_is_allowed_only_when_local_mode_is_explicit():
    with pytest.raises(McpClientConfigurationError):
        McpToolServerClient(
            service_name="data",
            url="http://mcp_data:8000/mcp",
            service_token="local-token",
            allowed_tools=frozenset({"list_properties"}),
        )

    configured = McpToolServerClient(
        service_name="data",
        url="http://mcp_data:8000/mcp",
        service_token="local-token",
        allowed_tools=frozenset({"list_properties"}),
        allow_compose_http=True,
    )
    assert configured.url == "http://mcp_data:8000/mcp"
