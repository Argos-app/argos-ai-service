"""Porta para gravar e paginar mensagens sanitizadas da conversa."""

from typing import Protocol
from uuid import UUID

from app.domain.sessions import ConversationMessage


class ConversationRepository(Protocol):
    async def append(self, message: ConversationMessage) -> None:
        """Acrescenta a mensagem de forma idempotente por ID/sequência."""

    async def list_before_sequence(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        before_sequence: int | None,
        limit: int,
    ) -> tuple[ConversationMessage, ...]:
        """Lê página anterior em ordem decrescente para o cursor interno.

        O caso de uso valida/decodifica o cursor HTTP e inverte os itens antes
        de devolvê-los para apresentação cronológica. Sem fronteira, seleciona
        as mensagens mais recentes; user_id sempre restringe titularidade.
        """

    async def list_after_sequence(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[ConversationMessage, ...]:
        """Lê mensagens posteriores em ordem crescente dentro de uma faixa."""
