"""Casos de consulta do agente Doutor para catálogos globais de medicamentos."""

from argos_mcp_data.application.access_policy import AccessPolicy
from argos_mcp_data.domain.access import AccessContext
from argos_mcp_data.domain.cattle import Page
from argos_mcp_data.domain.errors import AccessDeniedError
from argos_mcp_data.domain.medicine import (
    ListMedicineDocumentsRequest,
    ListMedicinesRequest,
    MedicineCatalogRecord,
    MedicineDetailDocument,
)
from argos_mcp_data.ports.medicine_document_repository import MedicineDocumentReadRepository
from argos_mcp_data.ports.medicine_repository import MedicineReadRepository


class MedicineQueryService:
    """Exige contexto assinado, agente Doutor e medicine.read antes das leituras."""

    def __init__(
        self,
        access_policy: AccessPolicy,
        catalog_repository: MedicineReadRepository,
        document_repository: MedicineDocumentReadRepository,
    ) -> None:
        self._access_policy = access_policy
        self._catalog_repository = catalog_repository
        self._document_repository = document_repository

    async def _authorize(self, context: AccessContext) -> None:
        if context.agent_id != "doutor":
            raise AccessDeniedError("ferramenta disponível somente ao agente Doutor")
        await self._access_policy.authorize(context, operation="medicine.read")

    async def list_medicines(
        self, context: AccessContext, request: ListMedicinesRequest
    ) -> Page[MedicineCatalogRecord]:
        await self._authorize(context)
        return await self._catalog_repository.list_medicines(request)

    async def list_medicine_documents(
        self, context: AccessContext, request: ListMedicineDocumentsRequest
    ) -> Page[MedicineDetailDocument]:
        await self._authorize(context)
        return await self._document_repository.list_documents(request)
