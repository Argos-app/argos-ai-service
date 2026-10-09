"""Configuração, abertura e fechamento dos recursos do runtime."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import asyncpg
import pytest
from argos_mcp_prediction import bootstrap

pytestmark = pytest.mark.asyncio


async def test_invalid_settings_never_open_pool(monkeypatch, configured_settings):
    create_pool = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    with pytest.raises(ValueError, match="MCP_PREDICTION_INTERNAL_SERVICE_TOKEN"):
        await bootstrap.create_runtime(replace(configured_settings, internal_service_token=None))
    create_pool.assert_not_awaited()


async def test_runtime_opens_read_only_pool_without_business_tools(
    monkeypatch, configured_settings, sql_pool
):
    pool, connection = sql_pool
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    runtime = await bootstrap.create_runtime(configured_settings)

    create_pool.assert_awaited_once_with(
        configured_settings.sql_dsn,
        server_settings={"default_transaction_read_only": "on"},
    )
    connection.fetchval.assert_awaited_once_with("SHOW default_transaction_read_only")
    assert await runtime.server.list_tools() == []
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
    server.run_streamable_http_async.side_effect = failure
    runtime = bootstrap.ServiceRuntime(pool, object(), server)
    monkeypatch.setattr(bootstrap, "create_runtime", AsyncMock(return_value=runtime))
    if failure is None:
        await bootstrap.run_server()
    else:
        with pytest.raises(type(failure)):
            await bootstrap.run_server()
    pool.close.assert_awaited_once()
