"""Consultas Mongo de histórico bovino sempre filtradas pelo escopo autorizado."""

from typing import Any
from uuid import UUID

from argos_mcp_data.domain.access import AuthorizedScope
from argos_mcp_data.domain.cattle_documents import (
    CattleDocumentsPage,
    CattleHistoryDocument,
    ListCattleDocumentsRequest,
)
from argos_mcp_data.domain.errors import AccessDeniedError
from argos_mcp_data.ports.cattle_document_repository import CattleDocumentReadRepository
from argos_mcp_data.ports.mongo_reader import MongoReader


class MongoCattleDocumentRepository(CattleDocumentReadRepository):
    """Busca documentos por propriedade autorizada; filtro de usuário nunca define o teto."""

    def __init__(self, reader: MongoReader, collection: str, id_format: str) -> None:
        if not collection or collection.startswith("system."):
            raise ValueError("coleção Mongo de histórico bovino inválida")
        if id_format not in {"string", "uuid_standard"}:
            raise ValueError("formato de UUID Mongo inválido")
        self._reader = reader
        self._collection = collection
        self._id_format = id_format

    def _encode_id(self, value: UUID) -> Any:
        return str(value) if self._id_format == "string" else value

    def _build_filter(self, scope: AuthorizedScope, request: ListCattleDocumentsRequest) -> dict:
        """O primeiro predicado é sempre derivado do contexto autenticado e validado."""
        query: dict[str, Any] = {
            "relational_property_id": {
                "$in": [self._encode_id(value) for value in scope.property_ids]
            }
        }
        if request.herd_id is not None:
            query["herd.herd_id"] = self._encode_id(request.herd_id)
        nested: dict[str, Any] = {}
        animal: dict[str, Any] = {}
        if request.animal_id is not None:
            animal["animal_exception_id"] = self._encode_id(request.animal_id)
        if request.ear_tag is not None:
            animal["ear_tag"] = {"$regex": _escape_regex(request.ear_tag), "$options": "i"}
        if request.animal_status is not None:
            animal["status"] = request.animal_status
        if animal:
            nested["animal"] = {"$elemMatch": animal}
        if request.lot_id is not None:
            nested["lot_id"] = self._encode_id(request.lot_id)
        if nested:
            query["lots"] = {"$elemMatch": nested}
        updated: dict[str, Any] = {}
        if request.updated_from is not None:
            updated["$gte"] = request.updated_from
        if request.updated_to is not None:
            updated["$lte"] = request.updated_to
        if updated:
            query["last_updated_at"] = updated
        return query

    async def list_documents(
        self, scope: AuthorizedScope, request: ListCattleDocumentsRequest
    ) -> CattleDocumentsPage:
        """Defesa em profundidade contra adapter falso ou consulta mal configurada."""
        raw_documents = await self._reader.find_many(
            self._collection,
            self._build_filter(scope, request),
            limit=request.page_size + 1,
            skip=request.offset,
            sort=(
                ("relational_property_id", 1),
                ("herd.herd_id", 1),
                ("_id", 1),
            ),
        )
        docs: list[CattleHistoryDocument] = []
        allowed = set(scope.property_ids)
        for raw in raw_documents:
            document = CattleHistoryDocument.model_validate(dict(raw))
            if document.relational_property_id not in allowed:
                raise AccessDeniedError("documento fora do escopo autorizado")
            docs.append(document)
        has_more = len(raw_documents) > request.page_size
        return CattleDocumentsPage(
            items=tuple(docs[: request.page_size]),
            page_size=request.page_size,
            next_offset=(request.offset + request.page_size if has_more else None),
            has_more=has_more,
        )


def _escape_regex(value: str) -> str:
    import re

    return re.escape(value)
