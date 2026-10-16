"""Portas para gerar resumos e extrair campos permitidos do perfil."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.memory import SessionSummary, UserMemory, UserMemoryChange
from app.domain.sessions import ConversationMessage, ExecutionEvent


class SessionSummaryGenerator(Protocol):
    async def generate(
        self,
        *,
        session_id: UUID,
        previous: SessionSummary | None,
        messages: Sequence[ConversationMessage],
        events: Sequence[ExecutionEvent],
        through_user_message_number: int,
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
    ) -> UserMemoryChange:
        """Propõe atualizações somente para tom e objetivo geral declarados."""
