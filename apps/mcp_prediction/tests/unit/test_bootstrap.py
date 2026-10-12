"""Configuração, abertura e fechamento dos recursos do runtime."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import uvicorn
from argos_mcp_prediction import bootstrap

pytestmark = pytest.mark.asyncio


async def test_invalid_settings_never_open_pool(monkeypatch, configured_settings):
    create_pool = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    with pytest.raises(ValueError, match="MCP_PREDICTION_INTERNAL_SERVICE_TOKEN"):
        await bootstrap.create_runtime(replace(configured_settings, internal_service_token=None))
    create_pool.assert_not_awaited()


async def test_runtime_opens_read_only_pool_and_registers_prediction_tool(
    monkeypatch, configured_settings, sql_pool
):
    pool, connection = sql_pool
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    runtime = await bootstrap.create_runtime(configured_settings)

    create_pool.assert_awaited_once_with(
        configured_settings.sql_dsn,
        server_settings={"default_transaction_read_only": "on"},
        command_timeout=configured_settings.sql_timeout_seconds,
    )
    connection.fetchval.assert_awaited_once_with("SHOW default_transaction_read_only")
    tools = await runtime.server.list_tools()
    assert [tool.name for tool in tools] == ["estimate_medicine_consumption"]
    pool.close.assert_not_awaited()
    await runtime.close()
    pool.close.assert_awaited_once()


@pytest.mark.parametrize("failure", [RuntimeError("probe failed"), asyncio.CancelledError()])
async def test_initialization_failure_closes_pool(
    monkeypatch, configured_settings, sql_pool, failure
):
    pool, connection = sql_pool
    connection.fetchval.side_effect = failure
    monkeypatch.setattr(asyncpg, "create_pool", AsyncMock(return_value=pool))
    with pytest.raises(type(failure)):
        await bootstrap.create_runtime(configured_settings)
    pool.close.assert_awaited_once()


async def test_runtime_rejects_writable_session_and_closes_pool(
    monkeypatch, configured_settings, sql_pool
):
    pool, connection = sql_pool
    connection.fetchval.return_value = "off"
    monkeypatch.setattr(asyncpg, "create_pool", AsyncMock(return_value=pool))
    with pytest.raises(RuntimeError, match="read-only"):
        await bootstrap.create_runtime(configured_settings)
    pool.close.assert_awaited_once()


@pytest.mark.parametrize(
    "failure", [None, RuntimeError("transport failed"), asyncio.CancelledError()]
)
async def test_server_exit_closes_runtime(monkeypatch, sql_pool, failure):
    pool, _ = sql_pool
    server = AsyncMock()
    server.settings.log_level = "INFO"
    runtime = bootstrap.ServiceRuntime(pool, object(), server, object(), object())
    serve = AsyncMock(side_effect=failure)
    monkeypatch.setattr(uvicorn, "Config", MagicMock())
    monkeypatch.setattr(uvicorn, "Server", MagicMock(return_value=MagicMock(serve=serve)))
    monkeypatch.setattr(bootstrap, "create_http_app", MagicMock())
    monkeypatch.setattr(bootstrap, "create_runtime", AsyncMock(return_value=runtime))
    if failure is None:
        await bootstrap.run_server()
    else:
        with pytest.raises(type(failure)):
            await bootstrap.run_server()
    pool.close.assert_awaited_once()


async def test_invalid_public_key_fails_before_pool_is_opened(monkeypatch, configured_settings):
    create_pool = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    with pytest.raises(ValueError, match="chave pública"):
        await bootstrap.create_runtime(
            replace(configured_settings, trusted_delegation_keys=(("key", "invalid"),))
        )
    create_pool.assert_not_awaited()
