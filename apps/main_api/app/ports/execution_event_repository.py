"""Porta para eventos sanitizados de chamadas de agentes e tools."""

from typing import Protocol

from app.domain.sessions import ExecutionEvent


class ExecutionEventWriteError(RuntimeError):
    """A interação não pode ser concluída sem registrar sua trilha de execução."""


class ExecutionEventRepository(Protocol):
    async def append(self, event: ExecutionEvent) -> None:
        """Grava uma chamada/evento idempotente por event_id."""
