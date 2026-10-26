"""HTTP em redes locais Compose/Kubernetes exige configuração local explícita."""

import pytest
from app.infrastructure.mcp.client import McpClientConfigurationError, McpToolServerClient


@pytest.mark.parametrize(
    ("host", "service_name", "tool"),
    [
        ("mcp_data", "data", "list_properties"),
        ("mcp-data", "data", "list_properties"),
        ("mcp_prediction", "prediction", "estimate_medicine_consumption"),
        ("mcp-prediction", "prediction", "estimate_medicine_consumption"),
        ("mcp_email", "email", "prepare_report"),
        ("mcp-email", "email", "prepare_report"),
    ],
)
def test_local_service_host_requires_explicit_local_mode(host, service_name, tool):
    url = f"http://{host}:8000/mcp"
    with pytest.raises(McpClientConfigurationError):
        McpToolServerClient(
            service_name=service_name,
            url=url,
            service_token="local-token",
            allowed_tools=frozenset({tool}),
        )

    configured = McpToolServerClient(
        service_name=service_name,
        url=url,
        service_token="local-token",
        allowed_tools=frozenset({tool}),
        allow_local_http=True,
    )
    assert configured.url == url
