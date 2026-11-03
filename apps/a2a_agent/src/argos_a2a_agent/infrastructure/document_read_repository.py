"""Consultas Qdrant e metadados do manifesto da geração publicada."""

import base64
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from argos_a2a_agent.domain.errors import (
    DocumentNotFoundError,
    IndexReadError,
    StaleDocumentRevisionError,
)
from argos_a2a_agent.domain.models import (
    CorpusManifest,
    DocumentChunk,
    DocumentFilter,
    DocumentMetadata,
    DocumentPage,
    DocumentSearchHit,
    RevisionHash,
)
from argos_a2a_agent.ports.manifest_repository import ManifestRepository
from argos_a2a_agent.ports.qdrant_reader import QdrantReader

_PAGE_SIZE = 50
_SCROLL_PAGE_SIZE = 100
_MAX_DOCUMENT_CHUNKS = 2_000
_MAX_PAGE_SPAN = 3


class QdrantDocumentReadRepository:
    """Implementa leitura do corpus global sem expor filtros Qdrant ao chamador.

    A coleção recebida pelo leitor deve ser o alias ativo. O manifesto fornece
    metadados e contagens de documentos distintos; Qdrant fornece trechos e
    relevância semântica.
    """

    def __init__(self, qdrant: QdrantReader, manifests: ManifestRepository) -> None:
        self._qdrant = qdrant
        self._manifests = manifests

    async def search(
        self,
        vector: Sequence[float],
        *,
        filters: DocumentFilter,
        limit: int,
        vector_name: str | None = None,
    ) -> Sequence[DocumentSearchHit]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit deve ser um inteiro entre 1 e 20")
        if not vector or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise ValueError("a consulta vetorial deve conter números finitos")

        manifest = await self._current_manifest(filters)
        filtered_documents = {
            item.document_id: item for item in manifest.documents if self._matches(item, filters)
        }
        if not filtered_documents:
            return ()

        try:
            records = await self._qdrant.query_points(
                vector,
                query_filter=self._qdrant_filter(filters, manifest.corpus_revision),
                limit=limit,
                **({"vector_name": vector_name} if vector_name is not None else {}),
            )
        except Exception as exc:
            raise IndexReadError("não foi possível consultar o índice documental") from exc

        hits = []
        for record in records:
            payload = self._payload(record)
            chunk = self._chunk(record, payload)
            metadata = filtered_documents.get(chunk.document_id)
            if metadata is None or metadata.revision_hash != chunk.revision_hash:
                raise IndexReadError("o índice contém trecho fora do manifesto publicado")
            if payload.get("corpus_revision") != manifest.corpus_revision:
                raise IndexReadError("o índice retornou trecho de outra revisão do corpus")
            score = record.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise IndexReadError("o índice retornou score inválido")
            try:
                hits.append(DocumentSearchHit(chunk=chunk, score=float(score)))
            except ValueError as exc:
                raise IndexReadError("o índice retornou resultado inválido") from exc
        return tuple(hits)

    async def get_chunks(
        self,
        document_id: UUID,
        *,
        revision_hash: RevisionHash,
        page_from: int,
        page_to: int,
    ) -> Sequence[DocumentChunk]:
        if not isinstance(document_id, UUID):
            raise ValueError("document_id deve ser UUID")
        if not isinstance(revision_hash, str) or len(revision_hash) != 64:
            raise ValueError("revision_hash inválido")
        if (
            isinstance(page_from, bool)
            or not isinstance(page_from, int)
            or isinstance(page_to, bool)
            or not isinstance(page_to, int)
            or page_from < 1
            or page_to < page_from
            or page_to - page_from + 1 > _MAX_PAGE_SPAN
        ):
            raise ValueError("o intervalo deve conter de uma a três páginas")

        manifest = await self._current_manifest()
        metadata = next(
            (item for item in manifest.documents if item.document_id == document_id),
            None,
        )
        if metadata is None:
            raise DocumentNotFoundError("documento não está no corpus publicado")
        if metadata.revision_hash != revision_hash:
            raise StaleDocumentRevisionError("a revisão do documento foi substituída")

        query_filter = self._qdrant_filter(
            DocumentFilter(document_ids=(document_id,), corpus_revision=manifest.corpus_revision),
            manifest.corpus_revision,
        )
        query_filter["must"].extend(
            [
                {
                    "key": "revision_hash",
                    "match": {"value": revision_hash},
                },
                {"key": "page_from", "range": {"lte": page_to}},
                {"key": "page_to", "range": {"gte": page_from}},
            ]
        )
        offset: str | int | None = None
        chunks: list[DocumentChunk] = []
        try:
            while True:
                records, next_offset = await self._qdrant.scroll(
                    query_filter=query_filter,
                    limit=_SCROLL_PAGE_SIZE,
                    offset=offset,
                )
                for record in records:
                    payload = self._payload(record)
                    chunk = self._chunk(record, payload)
                    if (
                        chunk.document_id != document_id
                        or chunk.revision_hash != revision_hash
                        or payload.get("corpus_revision") != manifest.corpus_revision
                    ):
                        raise IndexReadError("o índice retornou trecho fora da revisão solicitada")
                    if chunk.page_from <= page_to and chunk.page_to >= page_from:
                        chunks.append(chunk)
                        if len(chunks) > _MAX_DOCUMENT_CHUNKS:
                            raise IndexReadError("documento excede o limite de chunks indexados")
                if next_offset is None:
                    break
                if next_offset == offset:
                    raise IndexReadError("cursor de leitura Qdrant não avançou")
                offset = next_offset
        except IndexReadError:
            raise
        except Exception as exc:
            raise IndexReadError("não foi possível recuperar trechos do índice") from exc
        return tuple(sorted(chunks, key=lambda chunk: (chunk.page_from, chunk.chunk_index)))

    async def list_documents(
        self,
        *,
        filters: DocumentFilter,
        page_size: int,
        cursor: str | None,
    ) -> DocumentPage:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= _PAGE_SIZE
        ):
            raise ValueError("page_size deve ser um inteiro entre 1 e 50")
        manifest = await self._current_manifest(filters)
        filter_digest = self._filter_digest(filters)
        last_id = self._decode_cursor(cursor, manifest.corpus_revision, filter_digest)
        documents = sorted(
            (item for item in manifest.documents if self._matches(item, filters)),
            key=lambda item: str(item.document_id),
        )
        if last_id is not None:
            documents = [item for item in documents if str(item.document_id) > last_id]
        page = documents[: page_size + 1]
        has_more = len(page) > page_size
        selected = tuple(page[:page_size])
        next_cursor = (
            self._encode_cursor(
                selected[-1].document_id,
                manifest.corpus_revision,
                filter_digest,
            )
            if has_more and selected
            else None
        )
        return DocumentPage(
            documents=selected,
            next_cursor=next_cursor,
            complete=next_cursor is None,
        )

    async def count_documents(self, *, filters: DocumentFilter) -> int:
        manifest = await self._current_manifest(filters)
        return sum(self._matches(item, filters) for item in manifest.documents)

    async def _current_manifest(self, filters: DocumentFilter | None = None) -> CorpusManifest:
        manifest = await self._manifests.load()
        if manifest is None:
            raise IndexReadError("não há manifesto de corpus publicado")
        requested_revision = filters.corpus_revision if filters is not None else None
        if requested_revision is not None and requested_revision != manifest.corpus_revision:
            raise StaleDocumentRevisionError("a consulta solicita uma revisão antiga do corpus")
        return manifest

    @staticmethod
    def _matches(metadata: DocumentMetadata, filters: DocumentFilter) -> bool:
        if filters.document_ids is not None and metadata.document_id not in filters.document_ids:
            return False
        if (
            filters.document_types is not None
            and metadata.document_type not in filters.document_types
        ):
            return False
        if filters.jurisdictions is not None and metadata.jurisdiction not in filters.jurisdictions:
            return False
        if filters.document_date_from is not None and (
            metadata.document_date is None or metadata.document_date < filters.document_date_from
        ):
            return False
        if filters.document_date_to is not None and (
            metadata.document_date is None or metadata.document_date > filters.document_date_to
        ):
            return False
        return True

    @staticmethod
    def _qdrant_filter(filters: DocumentFilter, revision: RevisionHash) -> dict[str, Any]:
        must: list[dict[str, Any]] = [{"key": "corpus_revision", "match": {"value": revision}}]
        if filters.document_ids is not None:
            must.append(
                {
                    "key": "document_id",
                    "match": {"any": [str(item) for item in filters.document_ids]},
                }
            )
        if filters.document_types is not None:
            must.append(
                {
                    "key": "document_type",
                    "match": {"any": [item.value for item in filters.document_types]},
                }
            )
        if filters.jurisdictions is not None:
            must.append({"key": "jurisdiction", "match": {"any": list(filters.jurisdictions)}})
        if filters.document_date_from is not None or filters.document_date_to is not None:
            date_range: dict[str, str] = {}
            if filters.document_date_from is not None:
                date_range["gte"] = filters.document_date_from.isoformat()
            if filters.document_date_to is not None:
                date_range["lte"] = filters.document_date_to.isoformat()
            must.append({"key": "document_date", "range": date_range})
        return {"must": must}

    @staticmethod
    def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            raise IndexReadError("o índice retornou ponto sem metadados")
        return payload

    @staticmethod
    def _chunk(record: Mapping[str, Any], payload: Mapping[str, Any]) -> DocumentChunk:
        try:
            point_id = UUID(str(record["id"]))
            chunk = DocumentChunk(
                chunk_id=point_id,
                document_id=UUID(str(payload["document_id"])),
                revision_hash=payload["revision_hash"],
                chunk_index=payload["chunk_index"],
                page_from=payload["page_from"],
                page_to=payload["page_to"],
                text=payload["text"],
            )
            if any(
                isinstance(payload[field], bool) or not isinstance(payload[field], int)
                for field in ("chunk_index", "page_from", "page_to")
            ):
                raise ValueError("índice ou página inválidos")
            return chunk
        except (KeyError, TypeError, ValueError) as exc:
            raise IndexReadError("o índice retornou metadados de chunk inválidos") from exc

    @staticmethod
    def _filter_digest(filters: DocumentFilter) -> str:
        values = {
            "document_ids": sorted(str(item) for item in filters.document_ids or ()),
            "document_types": sorted(item.value for item in filters.document_types or ()),
            "jurisdictions": sorted(filters.jurisdictions or ()),
            "document_date_from": (
                filters.document_date_from.isoformat() if filters.document_date_from else None
            ),
            "document_date_to": (
                filters.document_date_to.isoformat() if filters.document_date_to else None
            ),
        }
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _encode_cursor(cls, document_id: UUID, revision: RevisionHash, filter_digest: str) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "revision": revision,
                "filter": filter_digest,
                "last": str(document_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(
        cursor: str | None, revision: RevisionHash, filter_digest: str
    ) -> str | None:
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2_048:
            raise ValueError("cursor inválido")
        try:
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {
                "v",
                "revision",
                "filter",
                "last",
            }:
                raise ValueError("cursor inválido")
            last_id = str(UUID(payload["last"]))
            if (
                type(payload["v"]) is not int
                or payload["v"] != 1
                or payload["revision"] != revision
                or payload["filter"] != filter_digest
            ):
                raise StaleDocumentRevisionError("cursor pertence a outra revisão ou filtro")
            return last_id
        except StaleDocumentRevisionError:
            raise
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("cursor inválido") from exc
