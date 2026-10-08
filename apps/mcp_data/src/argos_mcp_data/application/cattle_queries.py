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
from argos_mcp_data.domain.errors import AccessDeniedError
from argos_mcp_data.ports.cattle_repository import CattleReadRepository


class CattleQueryService:
    """Autoriza cada leitura detalhada antes de delegá-la ao repositório."""

    def __init__(self, access_policy: AccessPolicy, repository: CattleReadRepository) -> None:
        self._access_policy = access_policy
        self._repository = repository

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
