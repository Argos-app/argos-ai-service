"""Fluxos de sessão, deduplicação, contador e lease do serviço de aplicação."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.agent_schemas.inputs import QuestionFormattingRequest
from app.api.v1.dependencies.auth import Principal
from app.api.v1.models.interactions import AnswerInteraction, AnswerText, MessageInteraction
from app.application.session_service import MongoSessionService
from app.domain.sessions import (
    ConversationMessage,
    InteractionKind,
    InteractionRecord,
    ProcessingLease,
    SessionRecord,
)
from fastapi import HTTPException


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FakeSessionRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, SessionRecord] = {}
        self.fencing_tokens: dict[UUID, int] = {}

    async def create(self, session: SessionRecord) -> None:
        self.records[session.session_id] = session

    async def get_owned(self, session_id: UUID, user_id: UUID) -> SessionRecord | None:
        record = self.records.get(session_id)
        return record if record and record.user_id == user_id else None

    async def list_owned(self, user_id: UUID) -> list[SessionRecord]:
        return sorted(
            (record for record in self.records.values() if record.user_id == user_id),
            key=lambda record: record.created_at,
            reverse=True,
        )

    async def acquire_lease(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        owner_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ProcessingLease | None:
        record = await self.get_owned(session_id, user_id)
        if record is None:
            return None
        if record.processing_lease and record.processing_lease.expires_at > now:
            return None
        token = self.fencing_tokens.get(session_id, 0) + 1
        self.fencing_tokens[session_id] = token
        lease = ProcessingLease(owner_id, token, now, expires_at)
        self.records[session_id] = replace(
            record, processing_lease=lease, updated_at=now, version=record.version + 1
        )
        return lease

    async def renew_lease(
        self,
        session_id: UUID,
        lease: ProcessingLease,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        record = self.records.get(session_id)
        if record is None or record.processing_lease != lease:
            return False
        renewed = replace(lease, expires_at=expires_at)
        self.records[session_id] = replace(
            record, processing_lease=renewed, updated_at=now, version=record.version + 1
        )
        return True

    async def release_lease(self, session_id: UUID, lease: ProcessingLease) -> bool:
        record = self.records.get(session_id)
        if record is None or record.processing_lease != lease:
            return False
        now = _utcnow()
        self.records[session_id] = replace(
            record, processing_lease=None, updated_at=now, version=record.version + 1
        )
        return True


class FakeInteractionRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, InteractionRecord] = {}

    async def find_by_deduplication_key(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        kind: InteractionKind,
        deduplication_key: str,
    ) -> InteractionRecord | None:
        return next(
            (
                record
                for record in self.records.values()
                if record.session_id == session_id
                and record.user_id == user_id
                and record.kind == kind
                and record.deduplication_key == deduplication_key
            ),
            None,
        )

    async def get_owned(self, interaction_id: UUID, user_id: UUID) -> InteractionRecord | None:
        record = self.records.get(interaction_id)
        return record if record and record.user_id == user_id else None

    async def save_result(self, interaction: InteractionRecord, *, expected_version: int) -> bool:
        current = self.records.get(interaction.interaction_id)
        if current is None or current.version != expected_version:
            return False
        self.records[interaction.interaction_id] = interaction
        return True


class FakeConversationRepository:
    def __init__(self) -> None:
        self.messages: list[ConversationMessage] = []

    async def append(self, message: ConversationMessage) -> None:
        self.messages.append(message)

    async def list_before_sequence(self, *args, **kwargs) -> tuple[ConversationMessage, ...]:
        return ()


class FakeSessionWorkRepository:
    def __init__(self, sessions, interactions, conversations) -> None:
        self.sessions = sessions
        self.interactions = interactions
        self.conversations = conversations

    async def accept_user_interaction(self, interaction, message, lease, *, now):
        session = self.sessions.records[interaction.session_id]
        if session.processing_lease != lease:
            return None
        assigned = replace(
            message,
            sequence=session.next_message_sequence,
            user_message_number=session.user_message_count + 1,
        )
        self.sessions.records[session.session_id] = replace(
            session,
            next_message_sequence=session.next_message_sequence + 1,
            user_message_count=session.user_message_count + 1,
            updated_at=now,
            version=session.version + 1,
        )
        self.interactions.records[interaction.interaction_id] = interaction
        self.conversations.messages.append(assigned)
        return assigned

    async def finalize_interaction(self, interaction, message, lease, *, expected_version, now):
        session = self.sessions.records[interaction.session_id]
        current = self.interactions.records[interaction.interaction_id]
        if session.processing_lease != lease or current.version != expected_version:
            return None
        assigned = replace(message, sequence=session.next_message_sequence)
        self.sessions.records[session.session_id] = replace(
            session,
            next_message_sequence=session.next_message_sequence + 1,
            updated_at=now,
            version=session.version + 1,
        )
        self.interactions.records[interaction.interaction_id] = interaction
        self.conversations.messages.append(assigned)
        return assigned


class FakeGraph:
    def __init__(self) -> None:
        self.calls = 0
        self.question_id = str(uuid4())

    async def ainvoke(self, state, *, config):
        self.calls += 1
        if state.get("input_kind") == "answer":
            return {"final_answer": "Esclarecimento recebido."}
        return {
            "clarification_request": QuestionFormattingRequest(
                reason_code="MISSING_PERIOD",
                input_type="free_text",
                question_goal="Informe o período.",
            ),
            "clarification_question": "Qual período devo consultar?",
            "question_id": self.question_id,
        }

    async def aget_state(self, config):
        return SimpleNamespace(values={"question_id": self.question_id})


def _principal() -> Principal:
    return Principal(
        user_id=uuid4(),
        email="user@example.invalid",
        name="Test User",
        permission="USER",
        farm_ids=[uuid4()],
    )


def _service(graph=None):
    sessions = FakeSessionRepository()
    interactions = FakeInteractionRepository()
    conversations = FakeConversationRepository()
    work = FakeSessionWorkRepository(sessions, interactions, conversations)
    service = MongoSessionService(
        sessions=sessions,
        interactions=interactions,
        conversations=conversations,
        session_work=work,
        graph_provider=lambda: graph,
        renew_seconds=60,
    )
    return service, sessions, interactions, conversations


@pytest.mark.asyncio
async def test_create_and_list_sessions_are_scoped_to_principal():
    service, sessions, _, _ = _service()
    principal = _principal()
    created = await service.create_session(principal)

    listed = await service.list_sessions(principal)
    other = await service.list_sessions(_principal())

    assert created.session_id == listed[0].session_id
    assert created.farm_ids == principal.farm_ids
    assert other == []
    assert sessions.records[created.session_id].user_id == principal.user_id


@pytest.mark.asyncio
async def test_message_is_sanitized_and_replay_counts_once():
    graph = FakeGraph()
    service, sessions, _, conversations = _service(graph)
    principal = _principal()
    session = await service.create_session(principal)
    request = MessageInteraction(
        kind="message",
        client_message_id=uuid4(),
        message="Preciso consultar usuario@example.invalid",
    )

    first = await service.create_interaction(principal, session.session_id, request)
    replay = await service.create_interaction(principal, session.session_id, request)

    record = sessions.records[session.session_id]
    assert first == replay
    assert record.user_message_count == 1
    assert record.next_message_sequence == 3
    assert graph.calls == 1
    assert len(conversations.messages) == 2
    assert conversations.messages[0].sanitized_content.endswith("[EMAIL]")
    assert all(message.sequence == index for index, message in enumerate(conversations.messages, 1))


@pytest.mark.asyncio
async def test_answer_is_counted_once_and_conflicting_replay_is_rejected():
    graph = FakeGraph()
    service, sessions, _, conversations = _service(graph)
    principal = _principal()
    session = await service.create_session(principal)
    message = MessageInteraction(
        kind="message", client_message_id=uuid4(), message="Consulte o relatório"
    )
    question = await service.create_interaction(principal, session.session_id, message)
    answer = AnswerInteraction(
        kind="answer",
        interaction_id=question.interaction_id,
        question_id=UUID(graph.question_id),
        answer=AnswerText(kind="text", text="Últimos 30 dias"),
    )

    result = await service.create_interaction(principal, session.session_id, answer)
    replay = await service.create_interaction(principal, session.session_id, answer)

    assert result == replay
    assert sessions.records[session.session_id].user_message_count == 2
    assert sessions.records[session.session_id].next_message_sequence == 5
    assert len(conversations.messages) == 4
    conflicting = answer.model_copy(update={"answer": AnswerText(kind="text", text="Setembro")})
    with pytest.raises(HTTPException) as error:
        await service.create_interaction(principal, session.session_id, conflicting)
    assert error.value.status_code == 409
    assert graph.calls == 2
