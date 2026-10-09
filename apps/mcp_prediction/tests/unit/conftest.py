"""Dependências isoladas para validar a fundação sem banco de negócio."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from argos_mcp_prediction.settings import Settings


@pytest.fixture
def configured_settings():
    return Settings(
        sql_dsn="postgresql://readonly@localhost/prediction_test",
        internal_service_token="prediction-test-token",
        mcp_auth_issuer_url="http://127.0.0.1:8000",
        mcp_resource_server_url="http://127.0.0.1:8000/mcp",
        mcp_host="127.0.0.1",
    )


@pytest.fixture
def sql_pool():
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.fetchval = AsyncMock(return_value="on")
    connection.fetch = AsyncMock(return_value=[{"value": 7}])
    transaction = connection.transaction.return_value
    transaction.__aenter__ = AsyncMock()
    transaction.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock()
    return pool, connection
