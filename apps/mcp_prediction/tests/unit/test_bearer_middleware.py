"""Autenticação HTTP de serviço usada pelos MCPs."""

import httpx
import pytest

from packages.security.mcp_bearer import McpBearerMiddleware


@pytest.mark.asyncio
async def test_bearer_rejects_missing_invalid_and_duplicate_headers():
    calls = []

    async def app(scope, receive, send):
        calls.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    transport = httpx.ASGITransport(app=McpBearerMiddleware(app, "secret"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for headers in (
            [],
            [("Authorization", "Bearer wrong")],
            [("Authorization", "Bearer secret"), ("Authorization", "Bearer secret")],
        ):
            response = await client.post("/mcp", headers=headers)
            assert response.status_code == 401
        response = await client.post("/mcp", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 204
        response = await client.post("/mcp", headers={"Authorization": "bearer secret"})
        assert response.status_code == 204
    assert len(calls) == 2
