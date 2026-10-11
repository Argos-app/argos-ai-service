"""Porta de leitura e atualização concorrente do perfil do usuário."""

from typing import Protocol
from uuid import UUID

from app.domain.memory import UserMemory


class UserMemoryRepository(Protocol):
    async def get(self, user_id: UUID) -> UserMemory | None:
        """Recupera apenas o perfil persistido pelo usuário informado."""

    async def save_if_current(
        self,
        memory: UserMemory,
        *,
        expected_revision: int | None,
    ) -> bool:
        """Atualiza condicionalmente; False indica revisão concorrente."""
