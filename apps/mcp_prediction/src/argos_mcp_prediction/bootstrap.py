"""Composição e ciclo de vida da fundação do MCP de predição."""

from dataclasses import dataclass
from typing import Any

from argos_mcp_prediction.infrastructure import AsyncpgSqlReader
from argos_mcp_prediction.server import create_mcp_server
from argos_mcp_prediction.settings import Settings


@dataclass(slots=True)
class ServiceRuntime:
    sql_pool: Any
    sql_reader: AsyncpgSqlReader
    server: Any

    async def close(self) -> None:
        await self.sql_pool.close()


async def create_runtime(settings: Settings | None = None) -> ServiceRuntime:
    """Abre somente o pool de leitura; não cria objetos nem publica ferramentas."""

    configured = settings or Settings.from_env()
    configured.validate_runtime()
    server = create_mcp_server(configured)
    try:
        import asyncpg
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'asyncpg' para iniciar o serviço") from exc

    sql_pool = await asyncpg.create_pool(
        configured.sql_dsn,
        server_settings={"default_transaction_read_only": "on"},
    )
    try:
        async with sql_pool.acquire() as connection:
            read_only = await connection.fetchval("SHOW default_transaction_read_only")
            if str(read_only).lower() != "on":
                raise RuntimeError("a sessão PostgreSQL não está configurada como read-only")
        return ServiceRuntime(
            sql_pool=sql_pool,
            sql_reader=AsyncpgSqlReader(sql_pool),
            server=server,
        )
    except BaseException:
        await sql_pool.close()
        raise


async def run_server(settings: Settings | None = None) -> None:
    runtime = await create_runtime(settings)
    try:
        await runtime.server.run_streamable_http_async()
    finally:
        await runtime.close()
