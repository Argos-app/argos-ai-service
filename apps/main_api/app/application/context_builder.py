"""Monta contexto autorizado e limitado para uma nova execução do grafo."""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.agent_schemas.inputs import ContextMessage, SessionContext
from app.domain.sessions import ConversationMessage, InteractionStatus
from app.ports.conversation_repository import ConversationRepository
from app.ports.interaction_repository import InteractionRepository
from app.ports.session_summary_repository import SessionSummaryRepository
from app.ports.user_memory_repository import UserMemoryRepository
from app.session_config import (
    SESSION_CONTEXT_MAX_MESSAGES,
    SESSION_CONTEXT_MESSAGE_MAX_CHARS,
)


async def build_session_context(
    *,
    session_id: UUID,
    user_id: UUID,
    before_sequence: int,
    conversations: ConversationRepository,
    interactions: InteractionRepository,
    summaries: SessionSummaryRepository | None = None,
    user_memory: UserMemoryRepository | None = None,
) -> SessionContext:
    """Lê só dados anteriores à entrada atual e pertencentes ao titular.

    O resumo cobre o histórico até ``through_sequence``. Mensagens posteriores
    só entram quando a interação terminou, evitando reutilizar um turno que foi
    interrompido. O pedido atual segue separadamente no schema do agente.
    """

    summary, memory = await asyncio.gather(
        summaries.get_current(session_id) if summaries else _none(),
        user_memory.get(user_id) if user_memory else _none(),
    )
    messages = await conversations.list_before_sequence(
        session_id,
        user_id,
        before_sequence=before_sequence,
        limit=100,
    )
    if summary is not None:
        messages = tuple(
            message for message in messages if message.sequence > summary.through_sequence
        )

    interaction_ids = {message.interaction_id for message in messages}
    interaction_records = await asyncio.gather(
        *(interactions.get_owned(interaction_id, user_id) for interaction_id in interaction_ids)
    )
    terminal_ids = {
        record.interaction_id
        for record in interaction_records
        if record is not None
        and record.session_id == session_id
        and record.status in {InteractionStatus.COMPLETED, InteractionStatus.FAILED}
    }
    visible = sorted(
        (
            message
            for message in messages
            if message.interaction_id in terminal_ids
            and message.role.value in {"user", "assistant"}
            and message.sanitized_content.strip()
        ),
        key=lambda message: message.sequence,
    )[-SESSION_CONTEXT_MAX_MESSAGES:]

    return SessionContext(
        session_summary=summary.summary_text if summary else None,
        recent_messages=tuple(_context_message(message) for message in visible),
        user_tone=memory.tone if memory else None,
        user_general_goal=memory.general_goal if memory else None,
    )


def _context_message(message: ConversationMessage) -> ContextMessage:
    content = message.sanitized_content.strip()
    if len(content) > SESSION_CONTEXT_MESSAGE_MAX_CHARS:
        content = content[: SESSION_CONTEXT_MESSAGE_MAX_CHARS - 1].rstrip() + "…"
    return ContextMessage(role=message.role.value, content=content)


async def _none() -> None:
    return None
