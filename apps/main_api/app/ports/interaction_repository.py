"""Porta para persistir interações e deduplicar entradas repetidas."""

from typing import Protocol
from uuid import UUID

from app.domain.sessions import InteractionKind, InteractionRecord


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

    async def find_by_deduplication_key(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        kind: InteractionKind,
        deduplication_key: str,
    ) -> InteractionRecord | None:
        """Localiza replay sem revelar interações de outro titular."""

    async def save_result(self, interaction: InteractionRecord, *, expected_version: int) -> bool:
        """Salva transição/resultado somente se a versão não mudou."""
