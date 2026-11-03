"""Porta de consulta dos documentos globais de detalhes de medicamentos."""

from typing import Protocol

from argos_mcp_data.domain.cattle import Page
from argos_mcp_data.domain.medicine import ListMedicineDocumentsRequest, MedicineDetailDocument


class MedicineDocumentReadRepository(Protocol):
    async def list_documents(
        self, request: ListMedicineDocumentsRequest
    ) -> Page[MedicineDetailDocument]: ...
