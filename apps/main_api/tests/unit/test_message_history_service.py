"""Consulta paginada do histórico, titularidade e sanitização pública."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.api.v1.dependencies.auth import Principal
from app.application.message_history_service import MessageHistoryService
from app.domain.sessions import (
    ConversationMessage,
    MessageKind,
    MessageRole,
    SessionRecord,
)
from fastapi import HTTPException

USER_ID = UUID("00000000-0000-4000-8000-000000000001")
FARM_ID = UUID("00000000-0000-4000-8000-000000000101")
NOW = datetime(2026, 10, 11, tzinfo=UTC)


class FakeSessions:
    def __init__(self, session: SessionRecord | None) -> None:
        self.session = session

    async def get_owned(self, session_id: UUID, user_id: UUID):
        if (
            self.session
            and self.session.session_id == session_id
            and self.session.user_id == user_id
        ):
            return self.session
        return None


class FakeConversations:
    def __init__(self, messages: list[ConversationMessage]) -> None:
        self.messages = messages
        self.limits: list[int] = []

    async def list_before_sequence(self, session_id, user_id, *, before_sequence, limit):
        self.limits.append(limit)
        messages = [item for item in self.messages if item.session_id == session_id]
        if before_sequence is not None:
            messages = [item for item in messages if item.sequence < before_sequence]
        return tuple(sorted(messages, key=lambda item: item.sequence, reverse=True)[:limit])


def _principal(*, user_id: UUID = USER_ID, farm_ids: list[UUID] | None = None) -> Principal:
    return Principal(
        user_id=user_id,
        email="test@example.com",
        name="Teste",
        permission="USER",
        farm_ids=farm_ids if farm_ids is not None else [FARM_ID],
    )


def _session(*, user_id: UUID = USER_ID, farm_ids: tuple[UUID, ...] = (FARM_ID,)):
    return SessionRecord(
        session_id=UUID("00000000-0000-4000-8000-000000000011"),
        user_id=user_id,
        farm_ids=farm_ids,
        created_at=NOW,
        updated_at=NOW,
        next_message_sequence=6,
    )


def _message(sequence: int, role: MessageRole, *, payload: str | None = None):
    return ConversationMessage(
        message_id=uuid4(),
        session_id=_session().session_id,
        interaction_id=uuid4(),
        sequence=sequence,
        role=role,
        kind=(MessageKind.USER_INPUT if role == MessageRole.USER else MessageKind.ASSISTANT_ANSWER),
        sanitized_content=f"mensagem {sequence}",
        created_at=NOW,
        user_message_number=sequence if role == MessageRole.USER else None,
        payload_json=payload,
    )


@pytest.mark.asyncio
async def test_history_pages_are_stable_chronological_and_overfetch_one_item():
    session = _session()
    conversations = FakeConversations(
        [_message(i, MessageRole.USER if i % 2 else MessageRole.ASSISTANT) for i in range(1, 6)]
    )
    service = MessageHistoryService(sessions=FakeSessions(session), conversations=conversations)

    first = await service.list_messages(_principal(), session.session_id, limit=2, cursor=None)
    second = await service.list_messages(
        _principal(), session.session_id, limit=2, cursor=first.next_cursor
    )
    third = await service.list_messages(
        _principal(), session.session_id, limit=2, cursor=second.next_cursor
    )

    assert [item.sequence for item in first.items] == [4, 5]
    assert first.has_more and first.next_cursor
    assert [item.sequence for item in second.items] == [2, 3]
    assert second.has_more and second.next_cursor
    assert [item.sequence for item in third.items] == [1]
    assert not third.has_more and third.next_cursor is None
    assert conversations.limits == [3, 3, 3]


@pytest.mark.asyncio
async def test_history_rejects_other_owner_and_cursor_from_another_user():
    session = _session()
    service = MessageHistoryService(
        sessions=FakeSessions(session), conversations=FakeConversations([])
    )
    with pytest.raises(HTTPException) as missing:
        await service.list_messages(
            _principal(user_id=uuid4()), session.session_id, limit=50, cursor=None
        )
    assert missing.value.status_code == 404

    foreign_cursor = service_cursor(session.session_id, uuid4(), 2)
    with pytest.raises(HTTPException) as invalid:
        await service.list_messages(
            _principal(), session.session_id, limit=50, cursor=foreign_cursor
        )
    assert invalid.value.status_code == 400


@pytest.mark.asyncio
async def test_history_redacts_assistant_messages_after_partial_farm_access_revocation():
    session = _session(farm_ids=(FARM_ID, uuid4()))
    user_message = _message(1, MessageRole.USER)
    assistant_message = _message(2, MessageRole.ASSISTANT)
    service = MessageHistoryService(
        sessions=FakeSessions(session),
        conversations=FakeConversations([user_message, assistant_message]),
    )

    page = await service.list_messages(_principal(), session.session_id, limit=50, cursor=None)

    assert page.items[0].content == "mensagem 1"
    assert page.items[1].redacted is True
    assert "ocultado" in page.items[1].content


@pytest.mark.asyncio
async def test_history_preserves_structured_answers_and_redacts_credentials():
    session = _session()
    answer = _message(
        1,
        MessageRole.USER,
        payload=json.dumps(
            {
                "kind": "answer",
                "question_id": str(uuid4()),
                "answer": {"kind": "text", "text": "token=abc123 período"},
            }
        ),
    )
    assistant = _message(
        2,
        MessageRole.ASSISTANT,
        payload=json.dumps(
            {
                "response": {
                    "kind": "question",
                    "question_id": str(uuid4()),
                    "input_type": "single_choice",
                    "prompt": "Qual período?",
                    "reason_code": "MISSING_PERIOD",
                    "options": [{"option_id": "30d", "label": "Últimos 30 dias"}],
                    "expires_at": "2026-10-11T00:15:00Z",
                }
            }
        ),
    )
    service = MessageHistoryService(
        sessions=FakeSessions(session), conversations=FakeConversations([answer, assistant])
    )

    page = await service.list_messages(_principal(), session.session_id, limit=50, cursor=None)

    assert page.items[0].answer is not None
    assert page.items[0].answer.kind == "text"
    assert page.items[0].answer.text == "token=[REDACTED] período"
    assert page.items[0].question_id is not None
    assert page.items[1].response is not None
    assert page.items[1].response.kind == "question"
    assert page.items[1].response.options[0].option_id == "30d"
    assert page.items[1].response.options[0].label == "Últimos 30 dias"


def service_cursor(session_id: UUID, user_id: UUID, before_sequence: int) -> str:
    from app.application.message_history_service import _encode_cursor

    return _encode_cursor(session_id, user_id, before_sequence)
