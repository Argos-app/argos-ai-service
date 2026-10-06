"""Porta para operações de envio e idempotência."""

from typing import Protocol
from uuid import UUID

from argos_mcp_email.domain import DispatchRecord, DispatchState


class DispatchRepository(Protocol):
    async def ensure_indexes(self) -> None:
        """Garante unicidade da chave de idempotência no armazenamento."""

    async def find_by_idempotency_key(self, key: str) -> DispatchRecord | None:
        """Busca operação existente para uma chave de idempotência."""

    async def create_pending(
        self,
        *,
        operation_id: UUID,
        report_id: UUID,
        requested_by: UUID,
        idempotency_key: str,
    ) -> DispatchRecord:
        """Cria atomicamente uma operação pendente."""

    async def claim_for_send(self, operation_id: UUID) -> bool:
        """Adquire atomicamente o direito de chamar o provedor uma única vez."""

    async def update_state(
        self,
        operation_id: UUID,
        state: DispatchState,
        *,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> DispatchRecord:
        """Atualiza o estado de uma operação existente."""

    async def get(self, operation_id: UUID) -> DispatchRecord | None:
        """Recupera uma operação pelo identificador."""
