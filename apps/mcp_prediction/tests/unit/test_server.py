"""SDK MCP real com Bearer de serviço e nenhum acesso de negócio."""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from argos_mcp_prediction.adapters.delegation import authorize_mcp_call
from argos_mcp_prediction.server import (
    StaticServiceTokenVerifier,
    create_http_app,
    create_mcp_server,
)
from mcp.types import LATEST_PROTOCOL_VERSION

from packages.security.access_directory import UserPropertyAccess
from packages.security.access_policy import AccessPolicy
from packages.security.delegation import DelegationTokenVerifier

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


async def test_authenticated_http_initialization_and_prediction_tool_catalog(
    configured_settings, signed_delegation
):
    server = create_mcp_server(configured_settings, object())
    assert server.name == "argos-mcp-prediction"
    verifier = DelegationTokenVerifier(
        dict(configured_settings.trusted_delegation_keys),
        issuer=configured_settings.delegation_issuer,
        audience=configured_settings.mcp_resource_server_url,
        agent_operations={"analista": frozenset({"prediction.read"})},
    )
    app = create_http_app(server, verifier)
    token, _ = signed_delegation()
    headers = {"Accept": "application/json, text/event-stream", "X-Argos-Delegation": token}
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
        for value in (None, "invalid"):
            denied_headers = dict(headers)
            if value is None:
                del denied_headers["X-Argos-Delegation"]
            else:
                denied_headers["X-Argos-Delegation"] = value
            denied = await client.post(
                "/mcp",
                headers=denied_headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert denied.status_code == 401
        duplicate = await client.post(
            "/mcp",
            headers={**headers, "x-argos-delegation": token},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert duplicate.status_code == 401
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
        tools = result_json(listed)["result"]["tools"]
        assert [tool["name"] for tool in tools] == ["estimate_medicine_consumption"]
        assert list(tools[0]["inputSchema"]["properties"]) == ["payload"]


async def test_same_mcp_session_uses_each_request_context_and_revalidates_access(
    configured_settings,
    signed_delegation,
):
    server = create_mcp_server(configured_settings, object())
    verifier = DelegationTokenVerifier(
        dict(configured_settings.trusted_delegation_keys),
        issuer=configured_settings.delegation_issuer,
        audience=configured_settings.mcp_resource_server_url,
        agent_operations={"analista": frozenset({"prediction.read"})},
    )
    token_a, claims_a = signed_delegation(request_id="request-a")
    token_b, claims_b = signed_delegation(request_id="request-b")
    user_ids = {claims_a["sub"]: uuid4(), claims_b["sub"]: uuid4()}
    current = {
        claims_a["sub"]: {UUID(value) for value in claims_a["authorized_property_ids"]},
        claims_b["sub"]: {UUID(value) for value in claims_b["authorized_property_ids"]},
    }
    directory = AsyncMock()

    async def lookup(firebase_uid):
        await asyncio.sleep(0)
        properties = current[firebase_uid]
        if not properties:
            return None
        return UserPropertyAccess(user_id=user_ids[firebase_uid], property_ids=properties)

    directory.get_user_property_access.side_effect = lookup
    policy = AccessPolicy(directory)

    # Test-only tool exercises the SDK request metadata; runtime still has no business tools.
    @server.tool()
    async def inspect_authorized_scope() -> dict[str, str]:
        scope = await authorize_mcp_call(policy, operation="prediction.read")
        return {
            "user_id": str(scope.user_id),
            "property_id": str(scope.property_ids[0]),
            "request_id": scope.request_id,
        }

    app = create_http_app(server, verifier)
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {configured_settings.internal_service_token}",
        "X-Argos-Delegation": token_a,
    }
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client,
    ):
        initialized = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "scope-tests", "version": "1"},
                },
            },
        )
        protocol = result_json(initialized)["result"]["protocolVersion"]
        headers.update(
            {
                "Mcp-Session-Id": initialized.headers["mcp-session-id"],
                "MCP-Protocol-Version": protocol,
            }
        )
        notified = await client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert notified.status_code == 202

        async def call(token, message_id):
            return await client.post(
                "/mcp",
                headers={**headers, "X-Argos-Delegation": token},
                json={
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "method": "tools/call",
                    "params": {"name": "inspect_authorized_scope", "arguments": {}},
                },
            )

        first, second = await asyncio.gather(call(token_a, 2), call(token_b, 3))
        for response, claims in ((first, claims_a), (second, claims_b)):
            result = result_json(response)["result"]
            assert not result.get("isError")
            assert result["structuredContent"] == {
                "user_id": str(user_ids[claims["sub"]]),
                "property_id": claims["authorized_property_ids"][0],
                "request_id": claims["request_id"],
            }
        assert directory.get_user_property_access.await_count == 2
        # A valid signed token cannot retain access revoked after an earlier successful call.
        current[claims_a["sub"]] = set()
        denied = await call(token_a, 4)
        assert result_json(denied)["result"]["isError"]
        assert directory.get_user_property_access.await_count == 3
        # Session initialization never substitutes for a delegation on the current request.
        missing = {key: value for key, value in headers.items() if key != "X-Argos-Delegation"}
        denied = await client.post(
            "/mcp", headers=missing, json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"}
        )
        assert denied.status_code == 401
        assert directory.get_user_property_access.await_count == 3
