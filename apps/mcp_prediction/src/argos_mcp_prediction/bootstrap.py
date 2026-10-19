"""Composição e ciclo de vida da fundação do MCP de predição."""

from dataclasses import dataclass
from typing import Any

from argos_mcp_prediction.application import EstimateMedicineConsumption
from argos_mcp_prediction.infrastructure import (
    AsyncpgSqlReader,
    PostgresConsumptionRepository,
    PostgresCoverageRepository,
    UnknownCoverageRepository,
)
from argos_mcp_prediction.infrastructure.access_directory import PostgresAccessDirectory
from argos_mcp_prediction.server import (
    McpPredictionServer,
    create_http_app,
    create_mcp_server,
)
from argos_mcp_prediction.settings import Settings
from packages.security.access_policy import AccessPolicy
from packages.security.delegation import DelegationTokenVerifier


@dataclass(slots=True)
class ServiceRuntime:
    sql_pool: Any
    sql_reader: AsyncpgSqlReader
    server: Any
    access_policy: AccessPolicy
    delegation_verifier: DelegationTokenVerifier

    async def close(self) -> None:
        await self.sql_pool.close()


async def create_runtime(settings: Settings | None = None) -> ServiceRuntime:
    """Abre o pool read-only e compõe a única ferramenta de negócio."""

    configured = settings or Settings.from_env()
    configured.validate_runtime()
    delegation_verifier = DelegationTokenVerifier(
        dict(configured.trusted_delegation_keys),
        issuer=configured.delegation_issuer,
        audience=configured.mcp_resource_server_url,
        agent_operations={"analista": frozenset({"prediction.read"})},
        max_ttl_seconds=configured.delegation_max_ttl_seconds,
        clock_skew_seconds=configured.delegation_clock_skew_seconds,
    )
    try:
        import asyncpg
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'asyncpg' para iniciar o serviço") from exc

    sql_pool = await asyncpg.create_pool(
        configured.sql_dsn,
        server_settings={"default_transaction_read_only": "on"},
        command_timeout=configured.sql_timeout_seconds,
    )
    try:
        async with sql_pool.acquire() as connection:
            read_only = await connection.fetchval("SHOW default_transaction_read_only")
            if str(read_only).lower() != "on":
                raise RuntimeError("a sessão PostgreSQL não está configurada como read-only")
        sql_reader = AsyncpgSqlReader(sql_pool)
        access_policy = AccessPolicy(PostgresAccessDirectory(sql_reader))
        estimate_consumption = EstimateMedicineConsumption(
            access_policy=access_policy,
            consumption_repository=PostgresConsumptionRepository(sql_reader),
            coverage_repository=(
                PostgresCoverageRepository(sql_reader, configured.coverage_relation)
                if configured.coverage_relation
                else UnknownCoverageRepository()
            ),
            data_origin=configured.data_origin,
        )
        server = create_mcp_server(
            configured,
            McpPredictionServer(estimate_consumption, configured),
        )
        return ServiceRuntime(
            sql_pool=sql_pool,
            sql_reader=sql_reader,
            server=server,
            access_policy=access_policy,
            delegation_verifier=delegation_verifier,
        )
    except BaseException:
        await sql_pool.close()
        raise


async def run_server(settings: Settings | None = None) -> None:
    import uvicorn

    runtime = await create_runtime(settings)
    try:
        app = create_http_app(runtime.server, runtime.delegation_verifier)
        config = uvicorn.Config(
            app,
            host=runtime.server.settings.host,
            port=runtime.server.settings.port,
            log_level=runtime.server.settings.log_level.lower(),
        )
        await uvicorn.Server(config).serve()
    finally:
        await runtime.close()
