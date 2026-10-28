"""Composição das dependências do MCP de e-mail."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from argos_mcp_email.adapters.provider import SmtpMailProvider
from argos_mcp_email.adapters.recipients import RelationalRecipientDirectory
from argos_mcp_email.adapters.rendering import TemplateReportRenderer
from argos_mcp_email.adapters.source_verification import (
    SignedPayloadVerifier,
    TrustedEd25519KeyVerifier,
)
from argos_mcp_email.adapters.storage import MongoDispatchRepository, MongoReportRepository
from argos_mcp_email.application import GetReportStatus, PrepareReport, SendReport
from argos_mcp_email.infrastructure import AsyncpgSqlExecutor
from argos_mcp_email.server import McpEmailServer, create_mcp_server, register_email_tools
from argos_mcp_email.settings import Settings


@dataclass(slots=True)
class ServiceRuntime:
    """Recursos abertos e servidor MCP prontos para execução."""

    mongo_client: Any
    sql_pool: Any
    server: Any

    async def close(self) -> None:
        self.mongo_client.close()
        await self.sql_pool.close()


def build_prepare_report(
    *,
    mongo_collection: Any,
    sql_executor: Any,
    signature_checker: Callable[[bytes, str, str], bool],
    settings: Settings | None = None,
) -> PrepareReport:
    """Monta o caso de uso com adapters concretos.

        A criação dos clientes Mongo e SQL fica sob responsabilidade do processo de
        execução; o bootstrap recebe as conexões já configuradas.

    reservado para TTL e demais configurações do serviço."""

    del settings
    return PrepareReport(
        source_verifier=SignedPayloadVerifier(signature_checker),
        recipient_directory=RelationalRecipientDirectory(sql_executor),
        renderer=TemplateReportRenderer(),
        report_repository=MongoReportRepository(mongo_collection),
    )


def build_send_report(
    *,
    mongo_report_collection: Any,
    dispatch_repository: Any,
    settings: Settings,
) -> SendReport:
    """Monta o envio com o remetente configurado do Argos."""

    if not settings.smtp_host:
        raise ValueError("MCP_EMAIL_SMTP_HOST é obrigatório para envio")
    provider = SmtpMailProvider(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        use_tls=settings.smtp_use_tls,
    )
    return SendReport(
        report_repository=MongoReportRepository(mongo_report_collection),
        dispatch_repository=dispatch_repository,
        mail_provider=provider,
        sender=settings.no_reply_address,
    )


def build_get_report_status(*, dispatch_repository: Any) -> GetReportStatus:
    return GetReportStatus(dispatch_repository=dispatch_repository)


async def create_runtime(
    *,
    signature_checker: Callable[[bytes, str, str], bool] | None = None,
    settings: Settings | None = None,
) -> ServiceRuntime:
    """Abre Mongo/SQL, inicializa índices e compõe o servidor MCP."""

    configured = settings or Settings.from_env()
    if not configured.mongo_uri:
        raise ValueError("MCP_EMAIL_MONGO_URI é obrigatório para iniciar o serviço")
    if not configured.sql_dsn:
        raise ValueError("MCP_EMAIL_SQL_DSN é obrigatório para iniciar o serviço")
    checker = signature_checker or TrustedEd25519KeyVerifier(
        dict(configured.trusted_signature_keys)
    )
    if signature_checker is None and not configured.trusted_signature_keys:
        raise ValueError("MCP_EMAIL_TRUSTED_SIGNATURE_KEYS é obrigatório para iniciar o serviço")
    if not configured.internal_service_token:
        raise ValueError("MCP_EMAIL_INTERNAL_SERVICE_TOKEN é obrigatório para iniciar o serviço")
    if not configured.mcp_auth_issuer_url:
        raise ValueError("MCP_EMAIL_AUTH_ISSUER_URL é obrigatório para iniciar o serviço")
    if not configured.mcp_resource_server_url:
        raise ValueError("MCP_EMAIL_RESOURCE_SERVER_URL é obrigatório para iniciar o serviço")

    try:
        import asyncpg
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:
        raise RuntimeError(
            "instale as dependências motor, asyncpg e mcp para iniciar o serviço"
        ) from exc

    mongo_client = AsyncIOMotorClient(configured.mongo_uri, tz_aware=True)
    try:
        sql_pool = await asyncpg.create_pool(configured.sql_dsn)
    except Exception:
        mongo_client.close()
        raise

    database = mongo_client[configured.mongo_database]
    report_collection = database[configured.mongo_reports_collection]
    dispatch_repository = MongoDispatchRepository(database[configured.mongo_dispatch_collection])
    await dispatch_repository.ensure_indexes()

    prepare_report = build_prepare_report(
        mongo_collection=report_collection,
        sql_executor=AsyncpgSqlExecutor(sql_pool),
        signature_checker=checker,
        settings=configured,
    )
    send_report = build_send_report(
        mongo_report_collection=report_collection,
        dispatch_repository=dispatch_repository,
        settings=configured,
    )
    service = McpEmailServer(
        prepare_report,
        configured,
        send_report=send_report,
        get_report_status=build_get_report_status(dispatch_repository=dispatch_repository),
    )
    try:
        server = create_mcp_server(configured)
        register_email_tools(server, service)
    except Exception:
        mongo_client.close()
        await sql_pool.close()
        raise
    return ServiceRuntime(
        mongo_client=mongo_client,
        sql_pool=sql_pool,
        server=server,
    )


async def run_server(
    *,
    signature_checker: Callable[[bytes, str, str], bool] | None = None,
    settings: Settings | None = None,
) -> None:
    """Executa o transporte Streamable HTTP do MCP e fecha os recursos."""

    runtime = await create_runtime(
        signature_checker=signature_checker,
        settings=settings,
    )
    try:
        await runtime.server.run_streamable_http_async()
    finally:
        await runtime.close()
