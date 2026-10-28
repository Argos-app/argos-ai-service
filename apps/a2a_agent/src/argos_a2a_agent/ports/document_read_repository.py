"""Porta de consultas globais a documentos e chunks indexados."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from argos_a2a_agent.domain.models import (
    DocumentChunk,
    DocumentFilter,
    DocumentPage,
    DocumentSearchHit,
    RevisionHash,
)


class DocumentReadRepository(Protocol):
    async def search(
        self,
        vector: Sequence[float],
        *,
        filters: DocumentFilter,
        limit: int,
        vector_name: str | None = None,
    ) -> Sequence[DocumentSearchHit]:
        """Busca semântica limitada dentro da revisão global indicada."""

    async def get_chunks(
        self,
        document_id: UUID,
        *,
        revision_hash: RevisionHash,
        page_from: int,
        page_to: int,
    ) -> Sequence[DocumentChunk]:
        """Recupera trechos somente da revisão solicitada e das páginas limitadas."""

    async def list_documents(
        self,
        *,
        filters: DocumentFilter,
        page_size: int,
        cursor: str | None,
    ) -> DocumentPage:
        """Lista metadados globais com paginação limitada."""

    async def count_documents(self, *, filters: DocumentFilter) -> int:
        """Conta documentos distintos depois de aplicar todos os filtros."""
