"""Portas para gerar resumos e extrair campos permitidos do perfil."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.memory import SessionSummary, UserMemory
from app.domain.sessions import ConversationMessage


class SessionSummaryGenerator(Protocol):
    async def generate(
        self,
        *,
        previous: SessionSummary | None,
        messages: Sequence[ConversationMessage],
        max_tokens: int,
    ) -> SessionSummary:
        """Gera resumo incremental estruturado dentro do orçamento informado."""


class UserMemoryExtractor(Protocol):
    async def extract(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        sanitized_user_input: str,
        current: UserMemory | None,
    ) -> UserMemory | None:
        """Propõe atualizações somente para tom e objetivo geral declarados."""
