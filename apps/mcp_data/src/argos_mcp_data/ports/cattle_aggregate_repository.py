"""Porta de consultas agregadas, sempre vinculada a um escopo autorizado."""

from typing import Protocol

from argos_mcp_data.domain.access import AuthorizedScope
from argos_mcp_data.domain.cattle_aggregates import (
    AggregatePage,
    ExceptionSummaryRequest,
    FarmSummaryRequest,
)


class CattleAggregateReadRepository(Protocol):
    async def farm_summary(
        self, scope: AuthorizedScope, request: FarmSummaryRequest
    ) -> AggregatePage: ...

    async def exception_summary(
        self, scope: AuthorizedScope, request: ExceptionSummaryRequest
    ) -> AggregatePage: ...
