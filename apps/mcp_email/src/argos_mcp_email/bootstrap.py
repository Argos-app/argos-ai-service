"""Composição das dependências do MCP de e-mail."""

from collections.abc import Callable
from typing import Any

from argos_mcp_email.adapters.recipients import RelationalRecipientDirectory
from argos_mcp_email.adapters.rendering import TemplateReportRenderer
from argos_mcp_email.adapters.source_verification import SignedPayloadVerifier
from argos_mcp_email.adapters.storage import MongoReportRepository
from argos_mcp_email.application import PrepareReport
from argos_mcp_email.settings import Settings


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
