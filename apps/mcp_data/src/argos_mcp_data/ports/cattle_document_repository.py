"""Porta para consultas Mongo de documentos de histórico limitadas por propriedade."""

from typing import Protocol

from argos_mcp_data.domain.access import AuthorizedScope
from argos_mcp_data.domain.cattle_documents import CattleDocumentsPage, ListCattleDocumentsRequest


class CattleDocumentReadRepository(Protocol):
    async def list_documents(
        self, scope: AuthorizedScope, request: ListCattleDocumentsRequest
    ) -> CattleDocumentsPage: ...
