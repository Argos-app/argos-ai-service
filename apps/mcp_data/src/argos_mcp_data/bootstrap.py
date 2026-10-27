"""Composição e ciclo de vida do MCP de dados."""

from dataclasses import dataclass
from typing import Any

from argos_mcp_data.adapters.delegation import DATA_AGENT_OPERATIONS
from argos_mcp_data.adapters.report_signer import Ed25519LotReportSigner
from argos_mcp_data.application import AccessPolicy, CattleQueryService, MedicineQueryService
from argos_mcp_data.infrastructure import (
    AsyncpgSqlReader,
    MongoCattleDocumentRepository,
    MongoMedicineDocumentRepository,
    PostgresAccessDirectory,
    PostgresCattleAggregateRepository,
    PostgresCattleRepository,
    PostgresMedicineRepository,
    ReadOnlyMongoReader,
)
from argos_mcp_data.server import (
    create_http_app,
    create_mcp_server,
    register_cattle_tools,
    register_medicine_tools,
)
from argos_mcp_data.settings import Settings
from packages.security.delegation import DelegationTokenVerifier


@dataclass(slots=True)
class ServiceRuntime:
    mongo_client: Any
    sql_pool: Any
    sql_reader: AsyncpgSqlReader
    mongo_reader: ReadOnlyMongoReader
    access_policy: AccessPolicy
    delegation_verifier: DelegationTokenVerifier
    server: Any

    async def close(self) -> None:
        self.mongo_client.close()
        await self.sql_pool.close()


async def create_runtime(settings: Settings | None = None) -> ServiceRuntime:
    configured = settings or Settings.from_env()
    configured.validate_runtime()
    server = create_mcp_server(configured)
    delegation_verifier = DelegationTokenVerifier(
        dict(configured.trusted_delegation_keys),
        issuer=configured.delegation_issuer,
        audience=configured.mcp_resource_server_url,
        agent_operations=DATA_AGENT_OPERATIONS,
        max_ttl_seconds=configured.delegation_max_ttl_seconds,
        clock_skew_seconds=configured.delegation_clock_skew_seconds,
    )
    try:
        import asyncpg
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:
        raise RuntimeError(
            "instale as dependências asyncpg, motor e mcp para iniciar o serviço"
        ) from exc

    mongo_client = AsyncIOMotorClient(
        configured.mongo_uri, tz_aware=True, uuidRepresentation="standard"
    )
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

    sql_reader = AsyncpgSqlReader(sql_pool)
    try:
        access_policy = AccessPolicy(PostgresAccessDirectory(sql_reader))
        report_signer = Ed25519LotReportSigner(
            key_id=configured.report_signing_key_id,
            private_key_base64=configured.report_signing_private_key,
            ttl_seconds=configured.report_envelope_ttl_seconds,
        )
        mongo_reader = ReadOnlyMongoReader(database)
        document_repository = MongoCattleDocumentRepository(
            mongo_reader,
            configured.mongo_cattle_collection,
            configured.mongo_relational_id_format,
        )
        cattle_queries = CattleQueryService(
            access_policy,
            PostgresCattleRepository(sql_reader),
            document_repository,
            PostgresCattleAggregateRepository(sql_reader),
            report_signer,
        )
        register_cattle_tools(server, cattle_queries)
        medicine_queries = MedicineQueryService(
            access_policy,
            PostgresMedicineRepository(sql_reader),
            MongoMedicineDocumentRepository(
                mongo_reader,
                configured.mongo_medicine_collection,
                configured.mongo_medicine_id_format,
            ),
        )
        register_medicine_tools(server, medicine_queries)
    except Exception:
        await sql_pool.close()
        mongo_client.close()
        raise
    return ServiceRuntime(
        mongo_client=mongo_client,
        sql_pool=sql_pool,
        sql_reader=sql_reader,
        mongo_reader=mongo_reader,
        access_policy=access_policy,
        delegation_verifier=delegation_verifier,
        server=server,
    )


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
