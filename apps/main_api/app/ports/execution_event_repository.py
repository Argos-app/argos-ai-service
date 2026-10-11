"""Porta para eventos sanitizados de chamadas de agentes e tools."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.sessions import ExecutionEvent


class ExecutionEventWriteError(RuntimeError):
    """A interação não pode ser concluída sem registrar sua trilha de execução."""


class ExecutionEventRepository(Protocol):
    async def append(self, event: ExecutionEvent) -> None:
        """Grava uma chamada/evento idempotente por event_id."""

    async def list_for_interactions(
        self,
        session_id: UUID,
        user_id: UUID,
        interaction_ids: Sequence[UUID],
    ) -> tuple[ExecutionEvent, ...]:
        """Lista eventos concluídos de interações pertencentes ao titular."""
