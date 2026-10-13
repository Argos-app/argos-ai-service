"""Porta de extração de texto por página a partir de bytes PDF."""

from typing import Protocol

from argos_a2a_agent.domain.models import DocumentMetadata, ParsedDocument


class PdfParser(Protocol):
    async def parse(self, content: bytes, metadata: DocumentMetadata) -> ParsedDocument:
        """Extrai páginas sem modificar ou substituir o documento de origem."""
