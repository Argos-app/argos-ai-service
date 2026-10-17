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

MAX_CONTEXT_CHARS = 12_000
MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_SCAN_MESSAGES = 200
MAX_CONTEXT_MESSAGE_CHARS = 4_000


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
    after_sequence = summary.through_sequence if summary else 0
    through_sequence = before_sequence - 1
    collected: list[ConversationMessage] = []
    # Do not scan an unbounded transcript when no summary exists or it is stale.
    cursor = max(after_sequence, through_sequence - MAX_CONTEXT_SCAN_MESSAGES)
    while cursor < through_sequence:
        page = await conversations.list_after_sequence(
            session_id,
            user_id,
            after_sequence=cursor,
            through_sequence=through_sequence,
            limit=100,
        )
        if not page:
            break
        collected.extend(page)
        if page[-1].sequence <= cursor:
            raise ValueError("repositório não avançou a sequência de mensagens")
        cursor = page[-1].sequence
        if len(page) < 100:
            break
    messages = tuple(collected)

    interaction_ids = {message.interaction_id for message in messages}
    interaction_records = await asyncio.gather(
        *(interactions.get_owned(interaction_id, user_id) for interaction_id in interaction_ids)
    )
    terminal_ids = {
        record.interaction_id
        for record in interaction_records
        if record is not None
        and record.session_id == session_id
        and record.status
        in {InteractionStatus.COMPLETED, InteractionStatus.DEGRADED, InteractionStatus.FAILED}
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
    )

    summary_text = summary.summary_text if summary else None
    user_tone = memory.tone if memory else None
    user_goal = memory.general_goal if memory else None
    fixed_chars = sum(len(value or "") for value in (summary_text, user_tone, user_goal))
    if fixed_chars > MAX_CONTEXT_CHARS:
        summary_text = None
        fixed_chars = len(user_tone or "") + len(user_goal or "")

    remaining = max(0, MAX_CONTEXT_CHARS - fixed_chars)
    selected: list[ContextMessage] = []
    for message in reversed(visible[-MAX_CONTEXT_MESSAGES:]):
        content = message.sanitized_content.strip()
        if len(content) > MAX_CONTEXT_MESSAGE_CHARS or len(content) > remaining:
            continue
        selected.append(_context_message(message))
        remaining -= len(content)

    return SessionContext(
        session_summary=summary_text,
        recent_messages=tuple(reversed(selected)),
        user_tone=user_tone,
        user_general_goal=user_goal,
    )


def _context_message(message: ConversationMessage) -> ContextMessage:
    return ContextMessage(role=message.role.value, content=message.sanitized_content.strip())


async def _none() -> None:
    return None
