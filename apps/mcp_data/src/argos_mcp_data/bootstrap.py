"""Composição e ciclo de vida do MCP de dados."""

from dataclasses import dataclass
from typing import Any

from argos_mcp_data.infrastructure import AsyncpgSqlReader, ReadOnlyMongoReader
from argos_mcp_data.server import create_mcp_server
from argos_mcp_data.settings import Settings


@dataclass(slots=True)
class ServiceRuntime:
    mongo_client: Any
    sql_pool: Any
    sql_reader: AsyncpgSqlReader
    mongo_reader: ReadOnlyMongoReader
    server: Any

    async def close(self) -> None:
        self.mongo_client.close()
        await self.sql_pool.close()


async def create_runtime(settings: Settings | None = None) -> ServiceRuntime:
    configured = settings or Settings.from_env()
    configured.validate_runtime()
    server = create_mcp_server(configured)
    try:
        import asyncpg
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:
        raise RuntimeError(
            "instale as dependências asyncpg, motor e mcp para iniciar o serviço"
        ) from exc

    mongo_client = AsyncIOMotorClient(configured.mongo_uri, tz_aware=True)
    sql_pool = None
    try:
        database = mongo_client[configured.mongo_database]
        await database.command("ping")
        sql_pool = await asyncpg.create_pool(
            configured.sql_dsn,
            server_settings={"default_transaction_read_only": "on"},
        )
        async with sql_pool.acquire() as connection:
            read_only = await connection.fetchval("SHOW default_transaction_read_only")
            if str(read_only).lower() != "on":
                raise RuntimeError("a sessão PostgreSQL não está configurada como read-only")
    except Exception:
        if sql_pool is not None:
            await sql_pool.close()
        mongo_client.close()
        raise

    return ServiceRuntime(
        mongo_client=mongo_client,
        sql_pool=sql_pool,
        sql_reader=AsyncpgSqlReader(sql_pool),
        mongo_reader=ReadOnlyMongoReader(database),
        server=server,
    )


async def run_server(settings: Settings | None = None) -> None:
    runtime = await create_runtime(settings)
    try:
        await runtime.server.run_streamable_http_async()
    finally:
        await runtime.close()
