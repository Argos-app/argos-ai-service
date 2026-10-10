"""Adapter fail-closed enquanto uma fonte confiável de cobertura não existe."""

from datetime import UTC, date, datetime

from argos_mcp_prediction.domain import CoverageRecord, CoverageState
from argos_mcp_prediction.ports import CoverageReadRepository
from packages.security.access import AuthorizedScope
from packages.security.errors import AccessDeniedError


class UnknownCoverageRepository(CoverageReadRepository):
    """Não presume cobertura a partir dos eventos de aplicação disponíveis."""

    async def get_coverage(
        self,
        scope: AuthorizedScope,
        *,
        start: date,
        end: date,
    ) -> tuple[CoverageRecord, ...]:
        if (
            not isinstance(scope, AuthorizedScope)
            or scope.agent_id != "analista"
            or scope.operation != "prediction.read"
            or not 1 <= len(scope.property_ids) <= 100
            or len(set(scope.property_ids)) != len(scope.property_ids)
            or scope.expires_at.tzinfo is None
            or scope.expires_at <= datetime.now(UTC)
        ):
            raise AccessDeniedError("escopo autorizado inválido")
        if type(start) is not date or type(end) is not date or start > end:
            raise ValueError("janela de cobertura inválida")
        return tuple(
            CoverageRecord(
                property_id=property_id,
                state=CoverageState.UNKNOWN,
                reason="não há fonte confiável de cobertura histórica configurada",
            )
            for property_id in scope.property_ids
        )
