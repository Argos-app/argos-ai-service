"""Porta de armazenamento para sessões e consultas limitadas ao titular."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.sessions import ProcessingLease, SessionRecord


class SessionRepository(Protocol):
    async def create(self, session: SessionRecord) -> None:
        """Cria uma sessão; ID já existente é conflito, não sobrescrita."""

    async def get_owned(self, session_id: UUID, user_id: UUID) -> SessionRecord | None:
        """Recupera apenas quando a sessão pertence ao usuário informado."""

    async def list_owned(self, user_id: UUID) -> list[SessionRecord]:
        """Lista sessões do titular, mais recentes primeiro e com desempate estável."""

    async def acquire_lease(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        owner_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ProcessingLease | None:
        """Adquire atomicamente se livre/vencida; None indica sessão ocupada.

        O adapter deve conferir UTC e expires_at > now. Nova posse aumenta o
        fencing token, impedindo uma execução anterior de gravar estado tardio.
        """

    async def renew_lease(
        self,
        session_id: UUID,
        lease: ProcessingLease,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """Renova somente posse ainda válida com owner e fencing token iguais."""

    async def release_lease(self, session_id: UUID, lease: ProcessingLease) -> bool:
        """Libera somente a posse identificada pelo lease corrente."""
