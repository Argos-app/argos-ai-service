"""SDK MCP real com Bearer de serviço e nenhum acesso de negócio."""

import json

import httpx
import pytest
from argos_mcp_prediction.server import StaticServiceTokenVerifier, create_mcp_server
from mcp.types import LATEST_PROTOCOL_VERSION

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("token", ["", "wrong-token", "prediction-test-token ", "tóken"])
async def test_verifier_rejects_incorrect_tokens(configured_settings, token):
    verifier = StaticServiceTokenVerifier(
        configured_settings.internal_service_token, configured_settings.mcp_resource_server_url
    )
    assert await verifier.verify_token(token) is None


async def test_verifier_binds_accepted_token_to_service_scope_and_resource(configured_settings):
    verifier = StaticServiceTokenVerifier(
        configured_settings.internal_service_token, configured_settings.mcp_resource_server_url
    )
    token = await verifier.verify_token(configured_settings.internal_service_token)
    assert token.client_id == "argos-main-service"
    assert token.scopes == ["prediction:read"]
    assert token.resource == configured_settings.mcp_resource_server_url


def result_json(response):
    response.raise_for_status()
    if response.headers["content-type"].startswith("application/json"):
        return response.json()
    return json.loads(
        next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))
    )


async def test_authenticated_http_initialization_and_empty_catalog(configured_settings):
    server = create_mcp_server(configured_settings)
    assert server.name == "argos-mcp-prediction"
    app = server.streamable_http_app()
    headers = {"Accept": "application/json, text/event-stream"}
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client,
    ):
        for authorization in (None, "Bearer wrong-token", "Bearer "):
            denied_headers = dict(headers)
            if authorization is not None:
                denied_headers["Authorization"] = authorization
            denied = await client.post(
                "/mcp",
                headers=denied_headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert denied.status_code == 401

        headers["Authorization"] = f"Bearer {configured_settings.internal_service_token}"
        initialized = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "prediction-unit-tests", "version": "1"},
                },
            },
        )
        result = result_json(initialized)
        assert result["result"]["serverInfo"]["name"] == "argos-mcp-prediction"
        headers["Mcp-Session-Id"] = initialized.headers["mcp-session-id"]
        headers["MCP-Protocol-Version"] = result["result"]["protocolVersion"]
        notification = await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert notification.status_code == 202
        listed = await client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
        )
        assert result_json(listed)["result"]["tools"] == []
