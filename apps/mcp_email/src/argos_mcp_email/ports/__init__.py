"""Portas de saída do núcleo do MCP de e-mail."""

from .dispatch_repository import DispatchRepository
from .mail_provider import MailProvider, ProviderSendResult
from .recipient_directory import RecipientDirectory
from .report_renderer import RenderedReport, ReportRenderer
from .report_repository import ReportRepository
from .source_verifier import SourceVerifier

__all__ = [
    "DispatchRepository",
    "MailProvider",
    "ProviderSendResult",
    "RecipientDirectory",
    "ReportRenderer",
    "RenderedReport",
    "ReportRepository",
    "SourceVerifier",
]
