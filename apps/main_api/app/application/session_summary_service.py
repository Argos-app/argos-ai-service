"""Orquestra marcos de resumo e publicação concorrente por revisão."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from uuid import UUID

from app.domain.memory import SessionSummary
from app.domain.sessions import ConversationMessage, InteractionStatus
from app.ports.conversation_repository import ConversationRepository
from app.ports.execution_event_repository import ExecutionEventRepository
from app.ports.interaction_repository import InteractionRepository
from app.ports.memory_generators import SessionSummaryGenerator
from app.ports.session_summary_repository import SessionSummaryRepository
from app.session_config import (
    SESSION_SUMMARY_MAX_TOKENS,
    SUMMARY_EVERY_USER_MESSAGES,
)

logger = logging.getLogger(__name__)
SummaryGeneratorProvider = Callable[[], SessionSummaryGenerator | None]
TERMINAL_STATUSES = {
    InteractionStatus.COMPLETED,
    InteractionStatus.FAILED,
}


class SessionSummaryService:
    def __init__(
        self,
        *,
        summaries: SessionSummaryRepository,
        conversations: ConversationRepository,
        interactions: InteractionRepository,
        execution_events: ExecutionEventRepository,
        generator_provider: SummaryGeneratorProvider,
    ) -> None:
        self._summaries = summaries
        self._conversations = conversations
        self._interactions = interactions
        self._execution_events = execution_events
        self._generator_provider = generator_provider

    async def publish_due(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        user_message_count: int,
        through_sequence: int,
    ) -> SessionSummary | None:
        """Publica todos os marcos vencidos que tenham interações terminais."""

        target = user_message_count - user_message_count % SUMMARY_EVERY_USER_MESSAGES
        if target < SUMMARY_EVERY_USER_MESSAGES:
            return await self._summaries.get_current(session_id)

        generator = self._generator_provider()
        if generator is None:
            logger.warning("sumarizador indisponível para sessão %s; cobertura mantida", session_id)
            return await self._summaries.get_current(session_id)

        current = await self._summaries.get_current(session_id)
        while (current.through_user_message_number if current else 0) < target:
            previous_number = current.through_user_message_number if current else 0
            next_target = previous_number + SUMMARY_EVERY_USER_MESSAGES
            messages = await self._messages_since(
                session_id=session_id,
                user_id=user_id,
                after_sequence=current.through_sequence if current else 0,
                through_sequence=through_sequence,
            )
            if not messages:
                break

            interaction_ids = {message.interaction_id for message in messages}
            records = await asyncio.gather(
                *(
                    self._interactions.get_owned(interaction_id, user_id)
                    for interaction_id in interaction_ids
                )
            )
            statuses = {
                record.interaction_id: record.status
                for record in records
                if record is not None and record.session_id == session_id
            }
            user_numbers: dict[int, UUID] = {
                message.user_message_number: message.interaction_id
                for message in messages
                if message.role.value == "user" and message.user_message_number is not None
            }
            number_by_interaction = {
                interaction_id: number for number, interaction_id in user_numbers.items()
            }
            if any(
                number not in user_numbers
                or statuses.get(user_numbers[number]) not in TERMINAL_STATUSES
                for number in range(previous_number + 1, next_target + 1)
            ):
                break

            eligible_ids = {
                interaction_id
                for interaction_id, interaction_status in statuses.items()
                if interaction_status in TERMINAL_STATUSES
            }
            covered = tuple(
                message
                for message in messages
                if message.interaction_id in eligible_ids
                and number_by_interaction.get(message.interaction_id, next_target + 1)
                <= next_target
            )
            if not covered:
                break
            covered_ids = {message.interaction_id for message in covered}
            events = await self._execution_events.list_for_interactions(
                session_id, user_id, tuple(covered_ids)
            )
            generated = await generator.generate(
                session_id=session_id,
                previous=current,
                messages=covered,
                events=events,
                through_user_message_number=next_target,
                max_tokens=SESSION_SUMMARY_MAX_TOKENS,
            )
            if (
                generated.session_id != session_id
                or generated.revision != ((current.revision + 1) if current else 1)
                or generated.through_user_message_number != next_target
                or generated.through_sequence != max(message.sequence for message in covered)
            ):
                raise ValueError("sumarizador retornou cobertura ou revisão inconsistente")
            saved = await self._summaries.save_if_current(
                generated,
                expected_revision=current.revision if current else None,
            )
            if not saved:
                return await self._summaries.get_current(session_id)
            current = generated
        return current

    async def _messages_since(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        after_sequence: int,
        through_sequence: int,
    ) -> tuple[ConversationMessage, ...]:
        if through_sequence <= after_sequence:
            return ()
        collected: list[ConversationMessage] = []
        cursor = after_sequence
        while cursor < through_sequence:
            page = await self._conversations.list_after_sequence(
                session_id,
                user_id,
                after_sequence=cursor,
                through_sequence=through_sequence,
                limit=100,
            )
            if not page:
                break
            collected.extend(page)
            next_cursor = page[-1].sequence
            if next_cursor <= cursor:
                raise ValueError("repositório não avançou a sequência de mensagens")
            cursor = next_cursor
            if len(page) < 100:
                break
        return tuple(collected)
