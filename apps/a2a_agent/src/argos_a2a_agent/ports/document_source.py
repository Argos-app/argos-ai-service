"""Porta somente de leitura para PDFs globais de leis e termos."""

from typing import Protocol
from uuid import UUID

from argos_a2a_agent.domain.models import SourceDocument, SourceDocumentListing


class DocumentSource(Protocol):
    async def list_documents(self, *, cursor: str | None = None) -> SourceDocumentListing:
        """Lista documentos allowlisted e informa se a varredura está completa."""

    async def read_pdf(self, document_id: UUID) -> SourceDocument | None:
        """Lê o PDF identificado; a interface não oferece operações de escrita."""
