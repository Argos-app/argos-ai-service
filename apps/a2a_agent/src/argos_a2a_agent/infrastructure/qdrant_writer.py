"""Escrita restrita em coleções Qdrant de staging, isolada do leitor público."""

import math
import re
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from argos_a2a_agent.domain.errors import IndexWriteError
from argos_a2a_agent.domain.models import IndexedDocumentChunk, RevisionHash

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COLLECTION_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


class QdrantDocumentIndexWriter:
    """Grava em uma coleção por revisão e publica a revisão através de alias.

    Recebe um cliente Qdrant separado, criado com credencial de escrita própria.
    A superfície de leitura e o processo A2A não recebem essa instância.
    """

    def __init__(
        self,
        client: Any,
        *,
        active_alias: str,
        vector_size: int,
        distance: str,
    ) -> None:
        if not isinstance(active_alias, str) or not _COLLECTION_COMPONENT.fullmatch(active_alias):
            raise ValueError("active_alias deve conter de 1 a 120 caracteres alfanuméricos, _ ou -")
        if isinstance(vector_size, bool) or not isinstance(vector_size, int) or vector_size < 1:
            raise ValueError("vector_size deve ser um inteiro positivo")
        from qdrant_client.models import Distance

        try:
            self._distance = Distance(distance)
        except ValueError as exc:
            raise ValueError("distance deve ser uma métrica Qdrant suportada") from exc
        self._client = client
        self._active_alias = active_alias
        self._vector_size = vector_size

    async def upsert_chunks(
        self, corpus_revision: RevisionHash, chunks: Sequence[IndexedDocumentChunk]
    ) -> None:
        revision = self._validate_revision(corpus_revision)
        if not chunks:
            return
        point_ids = [item.chunk.chunk_id for item in chunks]
        if len(point_ids) != len(set(point_ids)):
            raise IndexWriteError("lote contém IDs de chunks duplicados")
        points = []
        from qdrant_client.models import PointStruct

        for item in chunks:
            if item.metadata.revision_hash != item.chunk.revision_hash:
                raise IndexWriteError("metadados não correspondem à revisão do chunk")
            if item.chunk.revision_hash != revision:
                raise IndexWriteError("chunk não pertence à geração solicitada")
            if len(item.vector) != self._vector_size or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in item.vector
            ):
                raise IndexWriteError("vetor do chunk diverge da dimensão configurada")
            points.append(
                PointStruct(
                    id=str(item.chunk.chunk_id),
                    vector=list(item.vector),
                    payload=self._payload(item, revision),
                )
            )
        collection = self._generation_collection(revision)
        try:
            await self._ensure_collection(collection)
            await self._client.upsert(collection_name=collection, points=points, wait=True)
        except IndexWriteError:
            raise
        except Exception as exc:
            raise IndexWriteError("não foi possível gravar chunks no staging Qdrant") from exc

    async def remove_document(self, corpus_revision: RevisionHash, document_id: UUID) -> None:
        revision = self._validate_revision(corpus_revision)
        if not isinstance(document_id, UUID):
            raise IndexWriteError("document_id deve ser UUID")
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        try:
            collection = self._generation_collection(revision)
            await self._ensure_collection(collection)
            await self._client.delete(
                collection_name=collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="document_id",
                                match=MatchValue(value=str(document_id)),
                            )
                        ]
                    )
                ),
                wait=True,
            )
        except IndexWriteError:
            raise
        except Exception as exc:
            raise IndexWriteError("não foi possível remover documento do staging Qdrant") from exc

    async def publish(self, corpus_revision: RevisionHash) -> None:
        revision = self._validate_revision(corpus_revision)
        from qdrant_client.models import (
            CreateAlias,
            CreateAliasOperation,
            DeleteAlias,
            DeleteAliasOperation,
        )

        collection = self._generation_collection(revision)
        try:
            await self._ensure_collection(collection)
            aliases_response = await self._client.get_aliases()
            aliases = [
                alias
                for alias in aliases_response.aliases
                if alias.alias_name == self._active_alias
            ]
            if len(aliases) > 1:
                raise IndexWriteError("mais de uma coleção está vinculada ao alias ativo")
            if aliases and aliases[0].collection_name == collection:
                return
            operations = []
            if aliases:
                operations.append(
                    DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=self._active_alias))
                )
            operations.append(
                CreateAliasOperation(
                    create_alias=CreateAlias(
                        collection_name=collection,
                        alias_name=self._active_alias,
                    )
                )
            )
            await self._client.update_collection_aliases(change_aliases_operations=operations)
        except IndexWriteError:
            raise
        except Exception as exc:
            raise IndexWriteError("não foi possível publicar a geração Qdrant") from exc

    async def discard(self, corpus_revision: RevisionHash) -> None:
        revision = self._validate_revision(corpus_revision)
        collection = self._generation_collection(revision)
        try:
            if not await self._client.collection_exists(collection_name=collection):
                return
            aliases_response = await self._client.get_aliases()
            if any(alias.collection_name == collection for alias in aliases_response.aliases):
                raise IndexWriteError("não é permitido descartar uma coleção publicada por alias")
            await self._client.delete_collection(collection_name=collection)
        except IndexWriteError:
            raise
        except Exception as exc:
            raise IndexWriteError("não foi possível descartar o staging Qdrant") from exc

    async def _ensure_collection(self, collection: str) -> None:
        if await self._client.collection_exists(collection_name=collection):
            info = await self._client.get_collection(collection_name=collection)
            vectors = info.config.params.vectors
            if (
                not hasattr(vectors, "size")
                or vectors.size != self._vector_size
                or vectors.distance != self._distance
            ):
                raise IndexWriteError("coleção de staging diverge da configuração vetorial")
            return
        from qdrant_client.models import VectorParams

        await self._client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=self._vector_size, distance=self._distance),
        )

    @staticmethod
    def _validate_revision(corpus_revision: str) -> str:
        if not isinstance(corpus_revision, str) or not _SHA256.fullmatch(corpus_revision):
            raise IndexWriteError("corpus_revision deve ser SHA-256 hexadecimal")
        return corpus_revision

    def _generation_collection(self, corpus_revision: str) -> str:
        return f"{self._active_alias}__g_{corpus_revision}"

    @staticmethod
    def _payload(item: IndexedDocumentChunk, corpus_revision: str) -> dict[str, Any]:
        metadata = item.metadata
        chunk = item.chunk
        return {
            "corpus_revision": corpus_revision,
            "document_id": str(metadata.document_id),
            "source_key": metadata.source_key,
            "revision_hash": metadata.revision_hash,
            "document_type": metadata.document_type.value,
            "title": metadata.title,
            "origin": metadata.origin,
            "jurisdiction": metadata.jurisdiction,
            "source_etag": metadata.source_etag,
            "source_modified_at": metadata.source_modified_at.isoformat()
            if metadata.source_modified_at
            else None,
            "document_date": metadata.document_date.isoformat() if metadata.document_date else None,
            "size_bytes": metadata.size_bytes,
            "uploaded_at": metadata.uploaded_at.isoformat() if metadata.uploaded_at else None,
            "updated_at": metadata.updated_at.isoformat() if metadata.updated_at else None,
            "legal_validity": metadata.legal_validity.value,
            "chunk_index": chunk.chunk_index,
            "page_from": chunk.page_from,
            "page_to": chunk.page_to,
            "text": chunk.text,
        }
