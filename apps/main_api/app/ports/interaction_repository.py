"""Porta para persistir interações e deduplicar entradas repetidas."""

from typing import Protocol
from uuid import UUID

from app.domain.sessions import InteractionRecord


class InteractionRepository(Protocol):
    async def create_or_get(self, interaction: InteractionRecord) -> tuple[InteractionRecord, bool]:
        """Retorna a interação canônica e se foi criada nesta chamada.

        A combinação session_id + kind + deduplication_key é única. Mensagens
        derivam a chave de client_message_id. Respostas derivam-na de
        question_id e do hash da resposta normalizada, pois o contrato de answer
        não oferece um ID de tentativa separado. Reutilizar a chave com payload
        diferente é conflito e não inicia outra execução.
        """

    async def get_owned(self, interaction_id: UUID, user_id: UUID) -> InteractionRecord | None:
        """Recupera a interação somente para seu titular."""

    async def save_result(self, interaction: InteractionRecord, *, expected_version: int) -> bool:
        """Salva transição/resultado somente se a versão não mudou."""
