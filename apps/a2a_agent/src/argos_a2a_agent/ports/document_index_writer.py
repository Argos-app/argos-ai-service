"""Porta de escrita técnica do índice, consumida somente pelo sincronizador."""

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from argos_a2a_agent.domain.models import (
    DocumentMetadata,
    IndexedDocumentChunk,
    RevisionHash,
)


class DocumentIndexWriter(Protocol):
    async def upsert_chunks(
        self, corpus_revision: RevisionHash, chunks: Sequence[IndexedDocumentChunk]
    ) -> None:
        """Grava chunks em uma geração ainda não publicada."""

    async def remove_document(self, corpus_revision: RevisionHash, document_id: UUID) -> None:
        """Remove um documento da geração em preparação."""

    async def copy_documents(
        self,
        source_revision: RevisionHash,
        target_revision: RevisionHash,
        documents: Mapping[UUID, DocumentMetadata],
    ) -> None:
        """Copia vetores de documentos intactos e atualiza seu payload técnico."""

    async def publish(self, corpus_revision: RevisionHash) -> None:
        """Publica atomicamente uma geração completa previamente preparada."""

    async def generation_exists(self, corpus_revision: RevisionHash) -> bool:
        """Confirma que uma geração persistida está disponível e compatível."""

    async def is_published(self, corpus_revision: RevisionHash) -> bool:
        """Confirma que o alias de leitura aponta para a geração informada."""

    async def unpublish(self, corpus_revision: RevisionHash) -> None:
        """Remove o alias somente se ele apontar para a geração informada."""

    async def discard(self, corpus_revision: RevisionHash) -> None:
        """Descarta uma geração incompleta sem alterar a geração publicada."""
