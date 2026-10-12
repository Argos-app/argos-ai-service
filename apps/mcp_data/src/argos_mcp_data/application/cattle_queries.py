"""Casos de consulta detalhada para o agente Fazenda."""

from uuid import UUID

from argos_mcp_data.application.access_policy import AccessPolicy
from argos_mcp_data.domain.access import AccessContext, AuthorizedScope
from argos_mcp_data.domain.cattle import (
    AnimalLotHistoryRecord,
    ApplicationAnimalRecord,
    ApplicationRecord,
    ExceptionAnimalRecord,
    HerdRecord,
    ListAnimalLotHistoryRequest,
    ListApplicationAnimalsRequest,
    ListApplicationsRequest,
    ListExceptionAnimalsRequest,
    ListHerdsRequest,
    ListLotsRequest,
    ListOccurrencesRequest,
    ListPropertiesRequest,
    LotRecord,
    OccurrenceRecord,
    Page,
    PropertyRecord,
)
from argos_mcp_data.domain.cattle_aggregates import (
    AggregatePage,
    ExceptionSummaryRequest,
    FarmSummaryRequest,
)
from argos_mcp_data.domain.cattle_documents import (
    CattleDocumentsPage,
    ListCattleDocumentsRequest,
)
from argos_mcp_data.domain.errors import AccessDeniedError
from argos_mcp_data.ports.cattle_aggregate_repository import CattleAggregateReadRepository
from argos_mcp_data.ports.cattle_document_repository import CattleDocumentReadRepository
from argos_mcp_data.ports.cattle_repository import CattleReadRepository


class CattleQueryService:
    """Autoriza leituras detalhadas e agregadas antes de consultar as fontes."""

    def __init__(
        self,
        access_policy: AccessPolicy,
        repository: CattleReadRepository,
        document_repository: CattleDocumentReadRepository,
        aggregate_repository: CattleAggregateReadRepository | None = None,
    ) -> None:
        self._access_policy = access_policy
        self._repository = repository
        self._document_repository = document_repository
        self._aggregate_repository = aggregate_repository

    async def _authorized_scope(
        self,
        context: AccessContext,
        selected_property_ids: tuple[UUID, ...] | None,
    ) -> AuthorizedScope:
        if context.agent_id != "fazenda":
            raise AccessDeniedError("ferramenta disponível somente ao agente Fazenda")
        return await self._access_policy.authorize(
            context,
            operation="cattle.read",
            selected_property_ids=selected_property_ids,
        )

    async def list_properties(
        self, context: AccessContext, request: ListPropertiesRequest
    ) -> Page[PropertyRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_properties(scope, request)

    async def list_herds(
        self, context: AccessContext, request: ListHerdsRequest
    ) -> Page[HerdRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_herds(scope, request)

    async def list_lots(self, context: AccessContext, request: ListLotsRequest) -> Page[LotRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_lots(scope, request)

    async def list_exception_animals(
        self, context: AccessContext, request: ListExceptionAnimalsRequest
    ) -> Page[ExceptionAnimalRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_exception_animals(scope, request)

    async def list_applications(
        self, context: AccessContext, request: ListApplicationsRequest
    ) -> Page[ApplicationRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_applications(scope, request)

    async def list_application_animals(
        self, context: AccessContext, request: ListApplicationAnimalsRequest
    ) -> Page[ApplicationAnimalRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_application_animals(scope, request)

    async def list_occurrences(
        self, context: AccessContext, request: ListOccurrencesRequest
    ) -> Page[OccurrenceRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_occurrences(scope, request)

    async def list_animal_lot_history(
        self, context: AccessContext, request: ListAnimalLotHistoryRequest
    ) -> Page[AnimalLotHistoryRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_animal_lot_history(scope, request)

    async def list_cattle_documents(
        self, context: AccessContext, request: ListCattleDocumentsRequest
    ) -> CattleDocumentsPage:
        """Autoriza a consulta e retorna documentos Mongo do escopo efetivo."""
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._document_repository.list_documents(scope, request)

    async def get_farm_summary(
        self, context: AccessContext, request: FarmSummaryRequest
    ) -> AggregatePage:
        scope = await self._authorized_scope(context, request.property_ids)
        if self._aggregate_repository is None:
            raise AccessDeniedError("consultas agregadas não estão configuradas")
        return await self._aggregate_repository.farm_summary(scope, request)

    async def summarize_exception_animals(
        self, context: AccessContext, request: ExceptionSummaryRequest
    ) -> AggregatePage:
        scope = await self._authorized_scope(context, request.property_ids)
        if self._aggregate_repository is None:
            raise AccessDeniedError("consultas agregadas não estão configuradas")
        return await self._aggregate_repository.exception_summary(scope, request)
