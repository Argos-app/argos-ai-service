"""O encerramento não deve descartar uma resposta MCP de e-mail já recebida."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from anyio import ClosedResourceError
from app.infrastructure.mcp.client import MCP_TOOL_ALLOWLISTS, McpToolServerClient


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_before_response", [False, True])
async def test_closed_stream_only_preserves_received_response(monkeypatch, failure_before_response):
    response = SimpleNamespace(isError=False, model_dump=lambda **_: {"status": "accepted"})

    class Session:
        async def call_tool(self, *_):
            if failure_before_response:
                raise ClosedResourceError
            return response

    @asynccontextmanager
    async def session(self, *_):
        yield Session()
        raise ClosedResourceError

    monkeypatch.setattr(McpToolServerClient, "_session", session)
    client = McpToolServerClient(
        service_name="email",
        url="http://127.0.0.1:8000/mcp",
        service_token="token",
        allowed_tools=MCP_TOOL_ALLOWLISTS["email"],
    )
    if failure_before_response:
        with pytest.raises(ClosedResourceError):
            await client.call_tool("send_report", {})
    else:
        assert await client.call_tool("send_report", {}) is response


@pytest.mark.asyncio
async def test_unrelated_close_error_is_not_ignored(monkeypatch):
    @asynccontextmanager
    async def session(self, *_):
        yield SimpleNamespace(call_tool=call_tool)
        raise ValueError("unexpected close error")

    async def call_tool(*_):
        return SimpleNamespace(isError=False, model_dump=lambda **_: {})

    monkeypatch.setattr(McpToolServerClient, "_session", session)
    client = McpToolServerClient(
        service_name="email",
        url="http://127.0.0.1:8000/mcp",
        service_token="token",
        allowed_tools=MCP_TOOL_ALLOWLISTS["email"],
    )
    with pytest.raises(ValueError, match="unexpected close error"):
        await client.call_tool("send_report", {})
