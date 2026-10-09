"""Parametrização e encerramento da transação de leitura."""

import pytest
from argos_mcp_prediction.infrastructure import AsyncpgSqlReader

pytestmark = pytest.mark.asyncio


async def test_reader_binds_parameters_inside_read_only_transaction(sql_pool):
    pool, connection = sql_pool
    reader = AsyncpgSqlReader(pool)
    query = "SELECT $1::text AS value"
    supplied = "'; DROP TABLE application; --"
    result = await reader.fetch_all(query, supplied)

    assert result == ({"value": 7},)
    connection.transaction.assert_called_once_with()
    connection.execute.assert_awaited_once_with("SET TRANSACTION READ ONLY")
    connection.fetch.assert_awaited_once_with(query, supplied)
    connection.transaction.return_value.__aexit__.assert_awaited_once_with(None, None, None)
    pool.acquire.return_value.__aexit__.assert_awaited_once_with(None, None, None)
    for name in ("execute", "insert", "update", "delete"):
        assert not hasattr(reader, name)


async def test_query_failure_propagates_and_releases_transaction_and_connection(sql_pool):
    pool, connection = sql_pool
    failure = RuntimeError("query failed")
    connection.fetch.side_effect = failure
    with pytest.raises(RuntimeError, match="query failed"):
        await AsyncpgSqlReader(pool).fetch_all("SELECT $1", 7)

    for manager in (connection.transaction.return_value, pool.acquire.return_value):
        manager.__aexit__.assert_awaited_once()
        assert manager.__aexit__.await_args.args[1] is failure


async def test_read_only_setup_failure_prevents_query(sql_pool):
    pool, connection = sql_pool
    connection.execute.side_effect = RuntimeError("read-only setup failed")
    with pytest.raises(RuntimeError, match="read-only setup failed"):
        await AsyncpgSqlReader(pool).fetch_all("SELECT $1", 7)
    connection.fetch.assert_not_awaited()
    pool.acquire.return_value.__aexit__.assert_awaited_once()
