"""Porta de leitura de evidências confiáveis de acompanhamento histórico."""

from datetime import date
from typing import Protocol

from argos_mcp_prediction.domain import CoverageRecord
from packages.security.access import AuthorizedScope


class CoverageReadRepository(Protocol):
    async def get_coverage(
        self,
        scope: AuthorizedScope,
        *,
        start: date,
        end: date,
    ) -> tuple[CoverageRecord, ...]:
        """Descreve a cobertura da janela inclusiva para cada propriedade do escopo.

        Evidências vêm de fonte confiável do serviço, não de parâmetros do agente.
        Primeiro/último evento e ausência de aplicações não confirmam acompanhamento.
        Sem evidência, retornar estado UNKNOWN e motivo, sem inventar fonte ou datas.
        Falha da fonte deve propagar erro, sem ser convertida em cobertura confirmada.

        A resposta contém exatamente um registro por propriedade autorizada, sem
        propriedades externas. Esta porta não pressupõe uma tabela de cobertura.
        """
