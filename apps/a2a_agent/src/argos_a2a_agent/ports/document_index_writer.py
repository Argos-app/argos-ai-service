"""Porta de escrita técnica do índice, consumida somente pelo sincronizador."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from argos_a2a_agent.domain.models import IndexedDocumentChunk, RevisionHash


class DocumentIndexWriter(Protocol):
    async def upsert_chunks(
        self, corpus_revision: RevisionHash, chunks: Sequence[IndexedDocumentChunk]
    ) -> None:
        """Grava chunks em uma geração ainda não publicada."""

    async def remove_document(self, corpus_revision: RevisionHash, document_id: UUID) -> None:
        """Remove um documento da geração em preparação."""

    async def publish(self, corpus_revision: RevisionHash) -> None:
        """Publica atomicamente uma geração completa previamente preparada."""

    async def discard(self, corpus_revision: RevisionHash) -> None:
        """Descarta uma geração incompleta sem alterar a geração publicada."""
