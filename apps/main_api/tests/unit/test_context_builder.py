from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.context_builder import build_session_context
from app.domain.memory import SessionSummary, SummaryFact, UserMemory
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
        assert limit == 100
        return tuple(
            message
            for message in sorted(self.messages, key=lambda item: item.sequence)
            if message.session_id == session_id
            and after_sequence < message.sequence <= through_sequence
        )


class FakeInteractions:
    def __init__(self, records):
        self.records = records

    async def get_owned(self, interaction_id, user_id):
        record = self.records.get(interaction_id)
        return record if record and record.user_id == user_id else None


class FakeSummaries:
    def __init__(self, summary):
        self.summary = summary

    async def get_current(self, session_id):
        return self.summary if self.summary.session_id == session_id else None


class FakeMemory:
    def __init__(self, memory):
        self.memory = memory

    async def get(self, user_id):
        return self.memory if self.memory.user_id == user_id else None


@pytest.mark.asyncio
async def test_context_uses_summary_profile_and_only_completed_prior_messages():
    now = datetime.now(UTC)
    user_id, session_id = uuid4(), uuid4()
    interactions = [
        InteractionRecord(
            interaction_id=uuid4(),
            session_id=session_id,
            user_id=user_id,
            deduplication_key=str(index),
            kind=InteractionKind.MESSAGE,
            sanitized_input=f"entrada {index}",
            input_hash=f"hash-{index}",
            status=status,
            created_at=now,
            updated_at=now,
            result_json="{}" if status == InteractionStatus.COMPLETED else None,
        )
        for index, status in enumerate(
            (
                InteractionStatus.COMPLETED,
                InteractionStatus.COMPLETED,
                InteractionStatus.INTERRUPTED,
            ),
            start=1,
        )
    ]
    messages = [
        ConversationMessage(
            message_id=uuid4(),
            session_id=session_id,
            interaction_id=interactions[0].interaction_id,
            sequence=1,
            role=MessageRole.USER,
            kind=MessageKind.USER_INPUT,
            sanitized_content="histórico já coberto pelo resumo",
            created_at=now,
            user_message_number=1,
        ),
        ConversationMessage(
            message_id=uuid4(),
            session_id=session_id,
            interaction_id=interactions[1].interaction_id,
            sequence=3,
            role=MessageRole.USER,
            kind=MessageKind.USER_INPUT,
            sanitized_content="Quero entender os últimos dados",
            created_at=now,
            user_message_number=2,
        ),
        ConversationMessage(
            message_id=uuid4(),
            session_id=session_id,
            interaction_id=interactions[1].interaction_id,
            sequence=4,
            role=MessageRole.ASSISTANT,
            kind=MessageKind.ASSISTANT_ANSWER,
            sanitized_content="Estes foram os dados consultados",
            created_at=now,
        ),
        ConversationMessage(
            message_id=uuid4(),
            session_id=session_id,
            interaction_id=interactions[2].interaction_id,
            sequence=5,
            role=MessageRole.USER,
            kind=MessageKind.USER_INPUT,
            sanitized_content="interação interrompida",
            created_at=now,
            user_message_number=3,
        ),
    ]
    summary = SessionSummary(
        session_id=session_id,
        revision=1,
        through_sequence=2,
        through_user_message_number=1,
        activity="Entender dados",
        important_facts=(SummaryFact("Fato anterior"),),
        decisions=(),
        pending_activity=None,
        limitations=(),
        summary_text="O usuário está entendendo os dados da fazenda.",
        token_count=9,
        updated_at=now,
    )
    memory = UserMemory(
        user_id=user_id,
        tone="simples e acolhedor",
        general_goal="entender os medicamentos",
        tone_source_session_id=session_id,
        tone_source_interaction_id=interactions[0].interaction_id,
        goal_source_session_id=session_id,
        goal_source_interaction_id=interactions[0].interaction_id,
        revision=1,
        updated_at=now,
    )

    context = await build_session_context(
        session_id=session_id,
        user_id=user_id,
        before_sequence=6,
        conversations=FakeConversations(messages),
        interactions=FakeInteractions({item.interaction_id: item for item in interactions}),
        summaries=FakeSummaries(summary),
        user_memory=FakeMemory(memory),
    )

    assert context.session_summary == summary.summary_text
    assert [(item.role, item.content) for item in context.recent_messages] == [
        ("user", "Quero entender os últimos dados"),
        ("assistant", "Estes foram os dados consultados"),
    ]
    assert context.user_tone == "simples e acolhedor"
    assert context.user_general_goal == "entender os medicamentos"

    other_user_context = await build_session_context(
        session_id=session_id,
        user_id=uuid4(),
        before_sequence=6,
        conversations=FakeConversations(messages),
        interactions=FakeInteractions({item.interaction_id: item for item in interactions}),
        summaries=FakeSummaries(summary),
        user_memory=FakeMemory(memory),
    )
    assert other_user_context.user_tone is None
    assert other_user_context.user_general_goal is None


@pytest.mark.asyncio
async def test_context_without_summary_is_bounded_by_recent_messages_and_char_budget():
    now = datetime.now(UTC)
    user_id, session_id = uuid4(), uuid4()
    records = []
    messages = []
    for index in range(30):
        interaction_id = uuid4()
        records.append(
            InteractionRecord(
                interaction_id=interaction_id,
                session_id=session_id,
                user_id=user_id,
                deduplication_key=str(index),
                kind=InteractionKind.MESSAGE,
                sanitized_input="pergunta",
                input_hash=f"hash-{index}",
                status=InteractionStatus.COMPLETED,
                created_at=now,
                updated_at=now,
                result_json="{}",
            )
        )
        messages.append(
            ConversationMessage(
                message_id=uuid4(),
                session_id=session_id,
                interaction_id=interaction_id,
                sequence=index * 2 + 1,
                role=MessageRole.USER,
                kind=MessageKind.USER_INPUT,
                sanitized_content=f"registro {index} " + "x" * 900,
                created_at=now,
                user_message_number=index + 1,
            )
        )
        messages.append(
            ConversationMessage(
                message_id=uuid4(),
                session_id=session_id,
                interaction_id=interaction_id,
                sequence=index * 2 + 2,
                role=MessageRole.ASSISTANT,
                kind=MessageKind.ASSISTANT_ANSWER,
                sanitized_content=f"resposta {index} " + "y" * 900,
                created_at=now,
            )
        )

    context = await build_session_context(
        session_id=session_id,
        user_id=user_id,
        before_sequence=61,
        conversations=FakeConversations(messages),
        interactions=FakeInteractions({item.interaction_id: item for item in records}),
    )

    assert len(context.recent_messages) <= 20
    assert sum(len(message.content) for message in context.recent_messages) <= 12_000
