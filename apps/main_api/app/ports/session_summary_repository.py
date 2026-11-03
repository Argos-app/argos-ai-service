"""Porta para obter e publicar resumos incrementais de sessão."""

from typing import Protocol
from uuid import UUID

from app.domain.memory import SessionSummary


class SessionSummaryRepository(Protocol):
    async def get_current(self, session_id: UUID) -> SessionSummary | None:
        """Recupera a revisão publicada mais recente da sessão."""

    async def save_if_current(
        self,
        summary: SessionSummary,
        *,
        expected_revision: int | None,
    ) -> bool:
        """Publica condicionalmente; False indica revisão concorrente."""
