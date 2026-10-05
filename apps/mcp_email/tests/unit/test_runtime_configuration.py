import pytest
from argos_mcp_email.bootstrap import create_runtime
from argos_mcp_email.settings import Settings


@pytest.mark.asyncio
async def test_runtime_requires_mongo_uri_before_opening_clients() -> None:
    settings = Settings(sql_dsn="postgresql://example")

    with pytest.raises(ValueError, match="MCP_EMAIL_MONGO_URI"):
        await create_runtime(signature_checker=lambda *_: True, settings=settings)


@pytest.mark.asyncio
async def test_runtime_requires_sql_dsn_before_opening_clients() -> None:
    settings = Settings(mongo_uri="mongodb://example")

    with pytest.raises(ValueError, match="MCP_EMAIL_SQL_DSN"):
        await create_runtime(signature_checker=lambda *_: True, settings=settings)
