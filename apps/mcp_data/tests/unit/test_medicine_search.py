from __future__ import annotations

import pytest
from argos_mcp_data.domain.medicine import ListMedicineDocumentsRequest, ListMedicinesRequest
from argos_mcp_data.infrastructure.medicine_document_repository import (
    MongoMedicineDocumentRepository,
)
from argos_mcp_data.infrastructure.medicine_repository import PostgresMedicineRepository


class _SqlReader:
    def __init__(self) -> None:
        self.query = ""
        self.parameters: tuple[object, ...] = ()

    async def fetch_all(self, query: str, *parameters: object):
        self.query = query
        self.parameters = parameters
        return []


class _MongoReader:
    def __init__(self) -> None:
        self.query = None

    async def find_many(self, collection, query, **_kwargs):
        del collection
        self.query = query
        return []


@pytest.mark.asyncio
async def test_catalog_search_matches_brand_or_active_ingredient():
    reader = _SqlReader()
    repository = PostgresMedicineRepository(reader)

    await repository.list_medicines(ListMedicinesRequest(search="Ivervet"))

    assert "lower(m.brand_name)" in reader.query
    assert " OR " in reader.query
    assert "lower(m.active_ingredient)" in reader.query
    assert reader.parameters[:2] == ("Ivervet", "Ivervet")


@pytest.mark.asyncio
async def test_mongo_medicine_document_search_matches_brand_or_ingredient():
    reader = _MongoReader()
    repository = MongoMedicineDocumentRepository(reader, "medicine_details", "string")

    await repository.list_documents(ListMedicineDocumentsRequest(search="Ivervet"))

    assert reader.query == {
        "$or": [
            {"brand_name": {"$regex": "Ivervet", "$options": "i"}},
            {"active_ingredient": {"$regex": "Ivervet", "$options": "i"}},
        ],
        "active": True,
    }
