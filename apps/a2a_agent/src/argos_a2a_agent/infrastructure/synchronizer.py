"""Sincronização versionada, fail-closed e serializada do corpus global."""

import asyncio
import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from argos_a2a_agent.domain.errors import (
    CorpusSynchronizationError,
    DocumentDomainError,
    SourceListingIncompleteError,
    SourceReadError,
)
from argos_a2a_agent.domain.models import (
    CorpusManifest,
    CorpusSyncResult,
    DocumentMetadata,
    SourceDocumentListing,
)
from argos_a2a_agent.ports.document_index_writer import DocumentIndexWriter
from argos_a2a_agent.ports.document_source import DocumentSource
from argos_a2a_agent.ports.manifest_repository import ManifestRepository

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_DOCUMENTS = 10_000
_MAX_LISTING_PAGES = 10_000


class DocumentCorpusSynchronizer:
    """Verifica toda a fonte e publica apenas uma geração integral preparada.

    O manifesto publicado define o estado autoritativo. Coleções anteriores
    permanecem disponíveis para rollback, mas consultas devem sempre restringir
    seus resultados à revisão retornada por este sincronizador.
    """

    def __init__(
        self,
        source: DocumentSource,
        manifests: ManifestRepository,
        processor: Any,
        index_writer: DocumentIndexWriter,
        *,
        processing_fingerprint: str,
    ) -> None:
        if not isinstance(processing_fingerprint, str) or not _SHA256.fullmatch(
            processing_fingerprint
        ):
            raise ValueError("processing_fingerprint deve ser SHA-256 hexadecimal")
        self._source = source
        self._manifests = manifests
        self._processor = processor
        self._index_writer = index_writer
        self._processing_fingerprint = processing_fingerprint
        self._lock = asyncio.Lock()

    async def ensure_current(self) -> CorpusSyncResult:
        """Espera uma sincronização em andamento terminar. Rejeitar imediatamente
        fazia consultas simultâneas sobre um corpus já publicado retornarem
        "unavailable" mesmo quando a sincronização concorrente terminava bem."""
        async with self._lock:
            try:
                return await self._synchronize()
            except DocumentDomainError:
                raise
            except Exception as exc:
                raise CorpusSynchronizationError("falha na sincronização do corpus") from exc

    async def _synchronize(self) -> CorpusSyncResult:
        """Recupera o último estado confirmado após interrupção entre a
        troca do alias e a publicação atômica do manifesto.
        Remove resíduos de uma execução interrompida antes de reutilizar a
        revisão determinística como nova geração de staging."""
        source_documents = await self._list_complete_source()
        current_documents = {item.document_id: item for item in source_documents}
        old_manifest = await self._manifests.load()
        old_documents = (
            {item.document_id: item for item in old_manifest.documents} if old_manifest else {}
        )

        rebuild_all = old_manifest is None or old_manifest.corpus_revision != self._revision(
            tuple(old_documents.values()) if old_manifest else ()
        )
        if old_manifest is not None and not rebuild_all:
            if not await self._index_writer.generation_exists(old_manifest.corpus_revision):
                raise CorpusSynchronizationError(
                    "a geração declarada pelo manifesto não existe no Qdrant"
                )
            if not await self._index_writer.is_published(old_manifest.corpus_revision):
                await self._index_writer.publish(old_manifest.corpus_revision)

        changed_ids = tuple(
            sorted(
                (
                    document_id
                    for document_id, document in current_documents.items()
                    if rebuild_all or old_documents.get(document_id) != document
                ),
                key=str,
            )
        )
        removed_ids = tuple(sorted(old_documents.keys() - current_documents.keys(), key=str))
        new_revision = self._revision(source_documents)
        if (
            old_manifest is not None
            and not rebuild_all
            and not changed_ids
            and not removed_ids
            and new_revision == old_manifest.corpus_revision
        ):
            return CorpusSyncResult(
                corpus_revision=old_manifest.corpus_revision,
                changed_document_ids=(),
                removed_document_ids=(),
                checked_at=datetime.now(UTC),
                updated=False,
            )

        reusable_documents: dict[UUID, DocumentMetadata] = {}
        if not rebuild_all:
            reusable_documents = {
                document_id: current
                for document_id, current in current_documents.items()
                if document_id in old_documents
                and old_documents[document_id].revision_hash == current.revision_hash
            }
        to_process = {
            document_id: document
            for document_id, document in current_documents.items()
            if document_id not in reusable_documents
        }
        new_manifest = CorpusManifest(
            corpus_revision=new_revision,
            documents=tuple(sorted(source_documents, key=lambda item: str(item.document_id))),
            generated_at=datetime.now(UTC),
        )

        if await self._index_writer.is_published(new_revision):
            await self._index_writer.unpublish(new_revision)
        await self._index_writer.discard(new_revision)

        try:
            if reusable_documents and old_manifest is not None:
                await self._index_writer.copy_documents(
                    old_manifest.corpus_revision,
                    new_revision,
                    reusable_documents,
                )
            for document_id in sorted(to_process, key=str):
                listed_metadata = to_process[document_id]
                source_document = await self._source.read_pdf(document_id)
                if source_document is None or source_document.metadata != listed_metadata:
                    raise SourceReadError(
                        "PDF lido não corresponde aos metadados da listagem atual"
                    )
                indexed_chunks = await self._processor.execute(source_document)
                if not indexed_chunks:
                    raise CorpusSynchronizationError(
                        "documento allowlisted não produziu chunks vetorizados"
                    )
                await self._index_writer.upsert_chunks(new_revision, indexed_chunks)
        except Exception as exc:
            await self._discard_staging(new_revision, exc)
            if isinstance(exc, DocumentDomainError):
                raise
            raise CorpusSynchronizationError("falha ao preparar a geração do corpus") from exc

        try:
            await self._index_writer.publish(new_revision)
        except Exception as publish_error:
            if await self._index_writer.is_published(new_revision):
                await self._rollback_publication(
                    old_manifest.corpus_revision if old_manifest else None,
                    new_revision,
                )
            else:
                await self._discard_staging(new_revision, publish_error)
            if isinstance(publish_error, DocumentDomainError):
                raise
            raise CorpusSynchronizationError("falha ao publicar a geração do corpus") from (
                publish_error
            )

        try:
            await self._manifests.save(new_manifest)
        except Exception as manifest_error:
            await self._rollback_publication(
                old_manifest.corpus_revision if old_manifest else None,
                new_revision,
            )
            if isinstance(manifest_error, DocumentDomainError):
                raise
            raise CorpusSynchronizationError(
                "falha ao confirmar o manifesto após publicação Qdrant"
            ) from manifest_error

        return CorpusSyncResult(
            corpus_revision=new_revision,
            changed_document_ids=changed_ids,
            removed_document_ids=removed_ids,
            checked_at=datetime.now(UTC),
            updated=True,
        )

    async def _list_complete_source(self) -> tuple[DocumentMetadata, ...]:
        documents: dict[UUID, DocumentMetadata] = {}
        cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(_MAX_LISTING_PAGES):
            listing: SourceDocumentListing = await self._source.list_documents(cursor=cursor)
            for document in listing.documents:
                previous = documents.get(document.document_id)
                if previous is not None:
                    raise SourceListingIncompleteError(
                        "a fonte repetiu um documento durante a paginação"
                    )
                documents[document.document_id] = document
                if len(documents) > _MAX_DOCUMENTS:
                    raise SourceListingIncompleteError("a fonte excede o limite de documentos")
            if listing.complete:
                return tuple(sorted(documents.values(), key=lambda item: str(item.document_id)))
            next_cursor = listing.continuation_cursor
            if not next_cursor or next_cursor in cursors or next_cursor == cursor:
                raise SourceListingIncompleteError("a listagem incompleta não forneceu cursor novo")
            cursors.add(next_cursor)
            cursor = next_cursor
        raise SourceListingIncompleteError("a fonte excede o limite de páginas de listagem")

    def _revision(self, documents: tuple[DocumentMetadata, ...]) -> str:
        normalized = [self._metadata_payload(item) for item in documents]
        normalized.sort(key=lambda item: item["document_id"])
        material = json.dumps(
            {
                "schema_version": 1,
                "processing_fingerprint": self._processing_fingerprint,
                "documents": normalized,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(material).hexdigest()

    @staticmethod
    def _metadata_payload(document: DocumentMetadata) -> dict[str, Any]:
        return {
            "document_id": str(document.document_id),
            "source_key": document.source_key,
            "title": document.title,
            "document_type": document.document_type.value,
            "origin": document.origin,
            "revision_hash": document.revision_hash,
            "size_bytes": document.size_bytes,
            "source_etag": document.source_etag,
            "source_modified_at": DocumentCorpusSynchronizer._timestamp(
                document.source_modified_at
            ),
            "jurisdiction": document.jurisdiction,
            "document_date": document.document_date.isoformat() if document.document_date else None,
            "uploaded_at": DocumentCorpusSynchronizer._timestamp(document.uploaded_at),
            "updated_at": DocumentCorpusSynchronizer._timestamp(document.updated_at),
            "legal_validity": document.legal_validity.value,
        }

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        return value.astimezone(UTC).isoformat() if value is not None else None

    async def _discard_staging(self, revision: str, cause: BaseException) -> None:
        try:
            if await self._index_writer.is_published(revision):
                raise CorpusSynchronizationError(
                    "não foi possível descartar geração que ainda está publicada"
                ) from cause
            await self._index_writer.discard(revision)
        except DocumentDomainError:
            raise
        except Exception as exc:
            raise CorpusSynchronizationError("falha ao limpar staging incompleto") from exc

    async def _rollback_publication(self, previous_revision: str | None, new_revision: str) -> None:
        try:
            if previous_revision is not None:
                if not await self._index_writer.generation_exists(previous_revision):
                    raise CorpusSynchronizationError(
                        "geração anterior ausente; rollback não pode ser confirmado"
                    )
                await self._index_writer.publish(previous_revision)
                if not await self._index_writer.is_published(previous_revision):
                    raise CorpusSynchronizationError("alias anterior não foi restaurado")
            else:
                await self._index_writer.unpublish(new_revision)
            await self._index_writer.discard(new_revision)
        except Exception as rollback_error:
            raise CorpusSynchronizationError(
                "falha na publicação e no rollback; corpus permanece indisponível"
            ) from rollback_error
