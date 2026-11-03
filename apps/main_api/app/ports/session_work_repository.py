"""Operações transacionais que vinculam uma interação ao estado da sessão."""

from datetime import datetime
from typing import Protocol

from app.domain.sessions import (
    ConversationMessage,
    InteractionRecord,
    ProcessingLease,
)


class SessionWorkRepository(Protocol):
    async def accept_user_interaction(
        self,
        interaction: InteractionRecord,
        message: ConversationMessage,
        lease: ProcessingLease,
        *,
        now: datetime,
    ) -> ConversationMessage | None:
        """Registra interação/mensagem e incrementa sequência/contador em transação."""

    async def finalize_interaction(
        self,
        interaction: InteractionRecord,
        message: ConversationMessage,
        lease: ProcessingLease,
        *,
        expected_version: int,
        now: datetime,
    ) -> ConversationMessage | None:
        """Publica resultado/mensagem final se a versão e lease continuarem atuais."""
