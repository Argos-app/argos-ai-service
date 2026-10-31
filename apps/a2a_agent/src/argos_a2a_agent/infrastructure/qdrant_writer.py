"""Escrita restrita em coleções Qdrant de staging, isolada do leitor público."""

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from argos_a2a_agent.domain.errors import IndexWriteError
from argos_a2a_agent.domain.models import (
    DocumentMetadata,
    IndexedDocumentChunk,
    RevisionHash,
)

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
        voyage_vector_size: int | None = None,
    ) -> None:
        if not isinstance(active_alias, str) or not _COLLECTION_COMPONENT.fullmatch(active_alias):
            raise ValueError("active_alias deve conter de 1 a 120 caracteres alfanuméricos, _ ou -")
        if isinstance(vector_size, bool) or not isinstance(vector_size, int) or vector_size < 1:
            raise ValueError("vector_size deve ser um inteiro positivo")
        if voyage_vector_size is not None and (
            isinstance(voyage_vector_size, bool)
            or not isinstance(voyage_vector_size, int)
            or voyage_vector_size < 1
        ):
            raise ValueError("voyage_vector_size deve ser um inteiro positivo")
        from qdrant_client.models import Distance

        try:
            self._distance = Distance(distance)
        except ValueError as exc:
            raise ValueError("distance deve ser uma métrica Qdrant suportada") from exc
        self._client = client
        self._active_alias = active_alias
        self._vector_size = vector_size
        self._voyage_vector_size = voyage_vector_size

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
            if len(item.vector) != self._vector_size or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in item.vector
            ):
                raise IndexWriteError("vetor do chunk diverge da dimensão configurada")
            if self._voyage_vector_size is not None and (
                item.voyage_vector is None
                or len(item.voyage_vector) != self._voyage_vector_size
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    for value in item.voyage_vector
                )
            ):
                raise IndexWriteError("vetor Voyage diverge da dimensão configurada")
            if self._voyage_vector_size is None and item.voyage_vector is not None:
                raise IndexWriteError("vetor Voyage não está configurado no writer")
            points.append(
                PointStruct(
                    id=str(item.chunk.chunk_id),
                    vector=self._point_vectors(item.vector, item.voyage_vector),
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

    async def copy_documents(
        self,
        source_revision: RevisionHash,
        target_revision: RevisionHash,
        documents: Mapping[UUID, DocumentMetadata],
    ) -> None:
        source = self._validate_revision(source_revision)
        target = self._validate_revision(target_revision)
        if source == target:
            raise IndexWriteError("gerações de origem e destino devem ser diferentes")
        if not documents:
            return
        for document_id, metadata in documents.items():
            if not isinstance(document_id, UUID) or metadata.document_id != document_id:
                raise IndexWriteError("mapa de cópia contém metadados com identidade divergente")

        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchValue,
            PointStruct,
        )

        source_collection = self._generation_collection(source)
        target_collection = self._generation_collection(target)
        requested_ids = set(documents)
        seen_ids: set[UUID] = set()
        offset = None
        try:
            if not await self._client.collection_exists(collection_name=source_collection):
                raise IndexWriteError("geração anterior não existe no Qdrant")
            await self._validate_collection(source_collection)
            await self._ensure_collection(target_collection)
            query_filter = Filter(
                must=[FieldCondition(key="corpus_revision", match=MatchValue(value=source))]
            )
            while True:
                points, next_offset = await self._client.scroll(
                    collection_name=source_collection,
                    scroll_filter=query_filter,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                copied_points = []
                for point in points:
                    payload = point.payload
                    if not isinstance(payload, dict):
                        raise IndexWriteError("ponto da geração anterior não possui payload")
                    try:
                        document_id = UUID(str(payload["document_id"]))
                    except (KeyError, ValueError, TypeError) as exc:
                        raise IndexWriteError("ponto anterior tem document_id inválido") from exc
                    metadata = documents.get(document_id)
                    if metadata is None:
                        continue
                    if payload.get("revision_hash") != metadata.revision_hash:
                        raise IndexWriteError(
                            "chunk copiado não corresponde à revisão atual do PDF"
                        )
                    vectors = self._validate_point_vectors(point.vector)
                    try:
                        chunk_index = payload["chunk_index"]
                        page_from = payload["page_from"]
                        page_to = payload["page_to"]
                        chunk_text = payload["text"]
                        if (
                            isinstance(chunk_index, bool)
                            or not isinstance(chunk_index, int)
                            or isinstance(page_from, bool)
                            or not isinstance(page_from, int)
                            or isinstance(page_to, bool)
                            or not isinstance(page_to, int)
                            or not isinstance(chunk_text, str)
                            or not chunk_text.strip()
                        ):
                            raise ValueError("metadados de chunk inválidos")
                    except (KeyError, ValueError) as exc:
                        raise IndexWriteError("chunk anterior tem payload incompleto") from exc
                    copied_payload = self._document_payload(metadata, target) | {
                        "chunk_index": chunk_index,
                        "page_from": page_from,
                        "page_to": page_to,
                        "text": chunk_text,
                    }
                    copied_points.append(
                        PointStruct(
                            id=point.id,
                            vector=vectors,
                            payload=copied_payload,
                        )
                    )
                    seen_ids.add(document_id)
                if copied_points:
                    await self._client.upsert(
                        collection_name=target_collection,
                        points=copied_points,
                        wait=True,
                    )
                if next_offset is None:
                    break
                if next_offset == offset:
                    raise IndexWriteError("cursor de leitura Qdrant não avançou")
                offset = next_offset
            missing = requested_ids - seen_ids
            if missing:
                raise IndexWriteError("geração anterior não contém chunks dos documentos intactos")
        except IndexWriteError:
            raise
        except Exception as exc:
            raise IndexWriteError("não foi possível copiar chunks intactos entre gerações") from exc

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

    async def generation_exists(self, corpus_revision: RevisionHash) -> bool:
        revision = self._validate_revision(corpus_revision)
        collection = self._generation_collection(revision)
        try:
            if not await self._client.collection_exists(collection_name=collection):
                return False
            await self._validate_collection(collection)
            return True
        except IndexWriteError:
            raise
        except Exception as exc:
            raise IndexWriteError("não foi possível verificar a geração Qdrant") from exc

    async def is_published(self, corpus_revision: RevisionHash) -> bool:
        revision = self._validate_revision(corpus_revision)
        collection = self._generation_collection(revision)
        try:
            aliases_response = await self._client.get_aliases()
            matches = [
                alias
                for alias in aliases_response.aliases
                if alias.alias_name == self._active_alias
            ]
            if len(matches) > 1:
                raise IndexWriteError("mais de uma coleção está vinculada ao alias ativo")
            return bool(matches and matches[0].collection_name == collection)
        except IndexWriteError:
            raise
        except Exception as exc:
            raise IndexWriteError("não foi possível verificar o alias ativo do Qdrant") from exc

    async def unpublish(self, corpus_revision: RevisionHash) -> None:
        revision = self._validate_revision(corpus_revision)
        collection = self._generation_collection(revision)
        from qdrant_client.models import DeleteAlias, DeleteAliasOperation

        try:
            aliases_response = await self._client.get_aliases()
            matches = [
                alias
                for alias in aliases_response.aliases
                if alias.alias_name == self._active_alias
            ]
            if len(matches) > 1:
                raise IndexWriteError("mais de uma coleção está vinculada ao alias ativo")
            if matches and matches[0].collection_name == collection:
                await self._client.update_collection_aliases(
                    change_aliases_operations=[
                        DeleteAliasOperation(
                            delete_alias=DeleteAlias(alias_name=self._active_alias)
                        )
                    ]
                )
        except IndexWriteError:
            raise
        except Exception as exc:
            raise IndexWriteError("não foi possível remover o alias da geração") from exc

    async def _ensure_collection(self, collection: str) -> None:
        if await self._client.collection_exists(collection_name=collection):
            await self._validate_collection(collection)
            return
        from qdrant_client.models import VectorParams

        if self._voyage_vector_size is None:
            vectors_config = VectorParams(size=self._vector_size, distance=self._distance)
        else:
            vectors_config = {
                "openai": VectorParams(size=self._vector_size, distance=self._distance),
                "voyage": VectorParams(size=self._voyage_vector_size, distance=self._distance),
            }
        await self._client.create_collection(
            collection_name=collection,
            vectors_config=vectors_config,
        )

    async def _validate_collection(self, collection: str) -> None:
        """Coleções antigas com vetor OpenAI único continuam publicáveis para
        rollback. O fingerprint novo força a reconstrução dual antes da busca."""
        info = await self._client.get_collection(collection_name=collection)
        vectors = info.config.params.vectors
        if self._voyage_vector_size is not None and isinstance(vectors, dict):
            expected = {"openai": self._vector_size, "voyage": self._voyage_vector_size}

            def matches(name: str, size: int) -> bool:
                params = vectors[name]
                actual_distance = getattr(params.distance, "value", params.distance)
                return params.size == size and actual_distance == self._distance.value

            if set(vectors) != set(expected) or not all(
                matches(name, size) for name, size in expected.items()
            ):
                raise IndexWriteError("coleção de staging diverge da configuração vetorial")
            return

        if (
            not hasattr(vectors, "size")
            or vectors.size != self._vector_size
            or vectors.distance != self._distance
        ):
            raise IndexWriteError("coleção de staging diverge da configuração vetorial")

    def _point_vectors(
        self,
        openai_vector: Sequence[float],
        voyage_vector: Sequence[float] | None,
    ) -> list[float] | dict[str, list[float]]:
        if self._voyage_vector_size is None:
            return list(openai_vector)
        if voyage_vector is None:
            raise IndexWriteError("chunk não contém vetor Voyage")
        return {"openai": list(openai_vector), "voyage": list(voyage_vector)}

    def _validate_point_vectors(self, raw_vectors: Any) -> list[float] | dict[str, list[float]]:
        if self._voyage_vector_size is None:
            if not self._valid_vector(raw_vectors, self._vector_size):
                raise IndexWriteError("vetor anterior diverge da dimensão configurada")
            return list(raw_vectors)
        if not isinstance(raw_vectors, Mapping):
            raise IndexWriteError("geração anterior não contém os vetores nomeados")
        expected = {"openai": self._vector_size, "voyage": self._voyage_vector_size}
        if set(raw_vectors) != set(expected):
            raise IndexWriteError("geração anterior diverge dos provedores configurados")
        normalized: dict[str, list[float]] = {}
        for name, size in expected.items():
            vector = raw_vectors[name]
            if not self._valid_vector(vector, size):
                raise IndexWriteError(f"vetor anterior {name} diverge da dimensão configurada")
            normalized[name] = list(vector)
        return normalized

    @staticmethod
    def _valid_vector(vector: Any, size: int) -> bool:
        return (
            isinstance(vector, Sequence)
            and not isinstance(vector, (str, bytes))
            and len(vector) == size
            and all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                for value in vector
            )
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
        chunk = item.chunk
        return QdrantDocumentIndexWriter._document_payload(item.metadata, corpus_revision) | {
            "chunk_index": chunk.chunk_index,
            "page_from": chunk.page_from,
            "page_to": chunk.page_to,
            "text": chunk.text,
        }

    @staticmethod
    def _document_payload(metadata: DocumentMetadata, corpus_revision: str) -> dict[str, Any]:
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
        }
