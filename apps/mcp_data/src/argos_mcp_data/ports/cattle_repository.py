"""Porta de consultas detalhadas que exige escopo autorizado em toda operação."""

from typing import Protocol

from argos_mcp_data.domain.access import AuthorizedScope
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


class CattleReadRepository(Protocol):
    async def list_properties(
        self, scope: AuthorizedScope, request: ListPropertiesRequest
    ) -> Page[PropertyRecord]: ...

    async def list_herds(
        self, scope: AuthorizedScope, request: ListHerdsRequest
    ) -> Page[HerdRecord]: ...

    async def list_lots(
        self, scope: AuthorizedScope, request: ListLotsRequest
    ) -> Page[LotRecord]: ...

    async def list_exception_animals(
        self, scope: AuthorizedScope, request: ListExceptionAnimalsRequest
    ) -> Page[ExceptionAnimalRecord]: ...

    async def list_applications(
        self, scope: AuthorizedScope, request: ListApplicationsRequest
    ) -> Page[ApplicationRecord]: ...

    async def list_application_animals(
        self, scope: AuthorizedScope, request: ListApplicationAnimalsRequest
    ) -> Page[ApplicationAnimalRecord]: ...

    async def list_occurrences(
        self, scope: AuthorizedScope, request: ListOccurrencesRequest
    ) -> Page[OccurrenceRecord]: ...

    async def list_animal_lot_history(
        self, scope: AuthorizedScope, request: ListAnimalLotHistoryRequest
    ) -> Page[AnimalLotHistoryRecord]: ...
