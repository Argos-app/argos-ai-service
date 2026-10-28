from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.session_summary_service import SessionSummaryService
from app.domain.memory import SessionSummary
from app.domain.sessions import (
    ConversationMessage,
    InteractionKind,
    InteractionRecord,
    InteractionStatus,
    MessageKind,
    MessageRole,
)


class FakeConversations:
    def __init__(self, messages):
        self.messages = messages

    async def list_after_sequence(
        self, session_id, user_id, *, after_sequence, through_sequence, limit
    ):
        del user_id
        return tuple(
            message
            for message in self.messages
            if message.session_id == session_id
            and after_sequence < message.sequence <= through_sequence
        )[:limit]


class FakeInteractions:
    def __init__(self, interactions):
        self.interactions = interactions

    async def get_owned(self, interaction_id, user_id):
        interaction = self.interactions.get(interaction_id)
        return interaction if interaction and interaction.user_id == user_id else None


class FakeEvents:
    def __init__(self):
        self.requested_interactions = []

    async def list_for_interactions(self, session_id, user_id, interaction_ids):
        self.requested_interactions.append((session_id, user_id, tuple(interaction_ids)))
        return ()


class FakeSummaryRepository:
    def __init__(self):
        self.current = None
        self.saved = []

    async def get_current(self, session_id):
        return self.current if self.current and self.current.session_id == session_id else None

    async def save_if_current(self, summary, *, expected_revision):
        current_revision = self.current.revision if self.current else None
        if current_revision != expected_revision:
            return False
        self.current = summary
        self.saved.append(summary)
        return True


class FakeGenerator:
    def __init__(self):
        self.calls = []
        self.fail = True

    async def generate(
        self,
        *,
        session_id,
        previous,
        messages,
        events,
        through_user_message_number,
        max_tokens,
    ):
        self.calls.append((previous, tuple(messages), tuple(events), through_user_message_number))
        if self.fail:
            self.fail = False
            raise RuntimeError("falha simulada no gerador")
        return SessionSummary(
            session_id=session_id,
            revision=previous.revision + 1 if previous else 1,
            through_sequence=max(message.sequence for message in messages),
            through_user_message_number=through_user_message_number,
            activity="Consultar aplicações.",
            important_facts=(),
            decisions=(),
            pending_activity=None,
            limitations=(),
            summary_text='{"activity":"Consultar aplicações."}',
            token_count=7,
            updated_at=datetime.now(UTC),
        )


def _records(session_id, user_id):
    now = datetime.now(UTC)
    interactions = {}
    messages = []
    for number in range(1, 16):
        interaction_id = uuid4()
        interactions[interaction_id] = InteractionRecord(
            interaction_id=interaction_id,
            session_id=session_id,
            user_id=user_id,
            deduplication_key=f"message-{number}",
            kind=InteractionKind.MESSAGE,
            sanitized_input=f"entrada {number}",
            input_hash=f"hash-{number}",
            status=InteractionStatus.COMPLETED,
            created_at=now,
            updated_at=now,
            result_json="{}",
        )
        messages.extend(
            (
                ConversationMessage(
                    message_id=uuid4(),
                    session_id=session_id,
                    interaction_id=interaction_id,
                    sequence=number * 2 - 1,
                    role=MessageRole.USER,
                    kind=MessageKind.USER_INPUT,
                    sanitized_content=f"entrada {number}",
                    created_at=now,
                    user_message_number=number,
                ),
                ConversationMessage(
                    message_id=uuid4(),
                    session_id=session_id,
                    interaction_id=interaction_id,
                    sequence=number * 2,
                    role=MessageRole.ASSISTANT,
                    kind=MessageKind.ASSISTANT_ANSWER,
                    sanitized_content=f"resultado {number}",
                    created_at=now,
                ),
            )
        )
    return messages, interactions


@pytest.mark.asyncio
async def test_summary_retries_failed_milestone_without_advancing_coverage():
    session_id, user_id = uuid4(), uuid4()
    messages, interactions = _records(session_id, user_id)
    repository = FakeSummaryRepository()
    events = FakeEvents()
    generator = FakeGenerator()
    service = SessionSummaryService(
        summaries=repository,
        conversations=FakeConversations(messages),
        interactions=FakeInteractions(interactions),
        execution_events=events,
        generator_provider=lambda: generator,
    )

    await service.publish_due(
        session_id=session_id,
        user_id=user_id,
        user_message_count=14,
        through_sequence=30,
    )
    assert generator.calls == []

    with pytest.raises(RuntimeError, match="falha simulada no gerador"):
        await service.publish_due(
            session_id=session_id,
            user_id=user_id,
            user_message_count=15,
            through_sequence=30,
        )
    assert repository.current is None
    assert repository.saved == []

    summary = await service.publish_due(
        session_id=session_id,
        user_id=user_id,
        user_message_count=15,
        through_sequence=30,
    )

    assert summary is repository.current
    assert summary.through_user_message_number == 15
    assert summary.through_sequence == 30
    assert repository.current.revision == 1
    assert len(generator.calls[-1][1]) == 30
    assert len(events.requested_interactions[-1][2]) == 15
