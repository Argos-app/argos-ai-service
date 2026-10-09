"""Porta de leitura do histórico agregado das propriedades autorizadas."""

from datetime import date
from typing import Protocol
from uuid import UUID

from argos_mcp_prediction.domain import MonthlyConsumptionRecord
from packages.security.access import AuthorizedScope


class ConsumptionReadRepository(Protocol):
    async def aggregate_monthly(
        self,
        scope: AuthorizedScope,
        *,
        start: date,
        end: date,
        medicine_ids: tuple[UUID, ...] | None = None,
    ) -> tuple[MonthlyConsumptionRecord, ...]:
        """Agrega toda a janela inclusiva por propriedade, medicamento, unidade e mês.

        scope é obrigatório e fornece a única seleção de propriedades. medicine_ids
        apenas restringe as aplicações desse escopo. Não usar uma página de eventos
        como base do agregado nem truncar silenciosamente os grupos do resultado.

        Ausência de aplicações retorna tupla vazia, sem provar consumo zero ou
        cobertura. Falha da fonte, dados inconsistentes e excesso de grupos devem
        propagar erros de domínio, nunca retornar um resultado parcial como completo.
        """
