"""Leitura paginada dos documentos Mongo de detalhes de medicamentos."""

import re
from typing import Any
from uuid import UUID

from argos_mcp_data.domain.cattle import Page
from argos_mcp_data.domain.medicine import ListMedicineDocumentsRequest, MedicineDetailDocument
from argos_mcp_data.ports.medicine_document_repository import MedicineDocumentReadRepository
from argos_mcp_data.ports.mongo_reader import MongoReader


class MongoMedicineDocumentRepository(MedicineDocumentReadRepository):
    """Busca documentos globais sem comparar seus campos com o catálogo SQL."""

    def __init__(self, reader: MongoReader, collection: str, id_format: str) -> None:
        if not collection or collection.startswith("system."):
            raise ValueError("coleção Mongo de medicamentos inválida")
        if id_format not in {"string", "uuid_standard"}:
            raise ValueError("formato de UUID Mongo inválido")
        self._reader = reader
        self._collection = collection
        self._id_format = id_format

    def _encode_id(self, value: UUID) -> Any:
        return str(value) if self._id_format == "string" else value

    def _build_filter(self, request: ListMedicineDocumentsRequest) -> dict[str, Any]:
        query: dict[str, Any] = {}
        if request.search is not None:
            query["$or"] = [
                {"brand_name": {"$regex": re.escape(request.search), "$options": "i"}},
                {
                    "active_ingredient": {
                        "$regex": re.escape(request.search),
                        "$options": "i",
                    }
                },
            ]
        if request.medicine_id is not None:
            query["relational_medicine_id"] = self._encode_id(request.medicine_id)
        if request.brand_name is not None:
            query["brand_name"] = {"$regex": re.escape(request.brand_name), "$options": "i"}
        if request.active_ingredient is not None:
            query["active_ingredient"] = {
                "$regex": re.escape(request.active_ingredient),
                "$options": "i",
            }
        if request.therapeutic_category is not None:
            query["therapeutic_category.name"] = {
                "$regex": re.escape(request.therapeutic_category),
                "$options": "i",
            }
        if request.needs_prescription is not None:
            query["needs_prescription"] = request.needs_prescription
        if request.active is not None:
            query["active"] = request.active
        updated: dict[str, Any] = {}
        if request.updated_from is not None:
            updated["$gte"] = request.updated_from
        if request.updated_to is not None:
            updated["$lte"] = request.updated_to
        if updated:
            query["last_updated_at"] = updated
        return query

    async def list_documents(
        self, request: ListMedicineDocumentsRequest
    ) -> Page[MedicineDetailDocument]:
        documents = await self._reader.find_many(
            self._collection,
            self._build_filter(request),
            limit=request.page_size + 1,
            skip=request.offset,
            sort=(
                ("brand_name", 1),
                ("relational_medicine_id", 1),
                ("_id", 1),
            ),
        )
        records = tuple(
            MedicineDetailDocument.model_validate(dict(document)) for document in documents
        )
        has_more = len(records) > request.page_size
        return Page[MedicineDetailDocument](
            items=records[: request.page_size],
            page_size=request.page_size,
            has_more=has_more,
            next_offset=request.offset + request.page_size if has_more else None,
        )
