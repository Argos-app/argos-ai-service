"""Composição das dependências do MCP de e-mail."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from argos_mcp_email.adapters.provider import SmtpMailProvider
from argos_mcp_email.adapters.recipients import RelationalRecipientDirectory
from argos_mcp_email.adapters.rendering import TemplateReportRenderer
from argos_mcp_email.adapters.source_verification import SignedPayloadVerifier
from argos_mcp_email.adapters.storage import MongoDispatchRepository, MongoReportRepository
from argos_mcp_email.application import GetReportStatus, PrepareReport, SendReport
from argos_mcp_email.infrastructure import AsyncpgSqlExecutor
from argos_mcp_email.server import McpEmailServer, create_mcp_server
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
    """

    del settings  # reservado para TTL e demais configurações do serviço.
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
    signature_checker: Callable[[bytes, str, str], bool],
    settings: Settings | None = None,
) -> ServiceRuntime:
    """Abre Mongo/SQL, inicializa índices e compõe o servidor MCP."""

    configured = settings or Settings.from_env()
    if not configured.mongo_uri:
        raise ValueError("MCP_EMAIL_MONGO_URI é obrigatório para iniciar o serviço")
    if not configured.sql_dsn:
        raise ValueError("MCP_EMAIL_SQL_DSN é obrigatório para iniciar o serviço")

    try:
        import asyncpg
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:
        raise RuntimeError(
            "instale as dependências motor, asyncpg e mcp para iniciar o serviço"
        ) from exc

    mongo_client = AsyncIOMotorClient(configured.mongo_uri)
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
        signature_checker=signature_checker,
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
    return ServiceRuntime(
        mongo_client=mongo_client,
        sql_pool=sql_pool,
        server=create_mcp_server(service),
    )


async def run_server(
    *,
    signature_checker: Callable[[bytes, str, str], bool],
    settings: Settings | None = None,
) -> None:
    """Executa o transporte stdio do servidor MCP e fecha os recursos."""

    runtime = await create_runtime(
        signature_checker=signature_checker,
        settings=settings,
    )
    try:
        await runtime.server.run_stdio_async()
    finally:
        await runtime.close()
