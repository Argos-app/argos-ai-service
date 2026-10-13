"""Porta de entrada para a barreira de atualização antes de ler o corpus."""

from typing import Protocol

from argos_a2a_agent.domain.models import CorpusSyncResult


class CorpusSynchronizer(Protocol):
    async def ensure_current(self) -> CorpusSyncResult:
        """Verifica a fonte e publica alterações antes de qualquer consulta."""
