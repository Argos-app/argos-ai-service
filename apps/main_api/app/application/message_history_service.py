"""Consulta paginada do histórico público de uma sessão."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import TypeAdapter, ValidationError

from app.api.v1.dependencies.auth import Principal
from app.api.v1.models.interactions import (
    AnswerValue,
    QuestionResponse,
    ResponseValue,
)
from app.api.v1.models.messages import SessionMessage, SessionMessagesPage
from app.application.session_service import _authorized_farm_ids
from app.domain.sessions import (
    ConversationMessage,
    MessageKind,
    MessageRole,
)
from app.ports.conversation_repository import ConversationRepository
from app.ports.session_repository import SessionRepository
from app.privacy import minimize_sensitive_memory_input

_REDACTED_CONTENT = (
    "[Conteúdo ocultado porque o acesso a uma ou mais fazendas desta sessão foi revogado.]"
)
_ANSWER_ADAPTER = TypeAdapter(AnswerValue)
_RESPONSE_ADAPTER = TypeAdapter(ResponseValue)


class MessageHistoryService:
    """Lê mensagens anteriores sem iniciar execução de agentes ou ferramentas."""

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        conversations: ConversationRepository,
    ) -> None:
        self._sessions = sessions
        self._conversations = conversations

    async def list_messages(
        self,
        principal: Principal,
        session_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> SessionMessagesPage:
        session = await self._sessions.get_owned(session_id, principal.user_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Sessão não encontrada"
            )

        authorized_farms = _authorized_farm_ids(session, principal.farm_ids)
        if not authorized_farms:
            # Mantém a mesma resposta para sessão inexistente e sem escopo atual.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Sessão não encontrada"
            )
        access_changed = len(authorized_farms) != len(session.farm_ids)

        before_sequence = None
        if cursor is not None:
            before_sequence = _decode_cursor(cursor, session_id, principal.user_id)
            if before_sequence >= session.next_message_sequence:
                raise _invalid_cursor()

        descending = await self._conversations.list_before_sequence(
            session_id,
            principal.user_id,
            before_sequence=before_sequence,
            limit=limit + 1,
        )
        has_more = len(descending) > limit
        page_descending = descending[:limit]
        items = [
            _public_message(
                message, redact=access_changed and message.role == MessageRole.ASSISTANT
            )
            for message in reversed(page_descending)
        ]
        next_cursor = (
            _encode_cursor(session_id, principal.user_id, page_descending[-1].sequence)
            if has_more and page_descending
            else None
        )
        return SessionMessagesPage(
            session_id=session_id,
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )


def _encode_cursor(session_id: UUID, user_id: UUID, before_sequence: int) -> str:
    payload = {
        "v": 1,
        "session": _binding_id(session_id),
        "owner": _binding_id(user_id),
        "before_sequence": before_sequence,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, session_id: UUID, user_id: UUID) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload: Any = json.loads(
            base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "session", "owner", "before_sequence"}
            or payload["v"] != 1
            or payload["session"] != _binding_id(session_id)
            or payload["owner"] != _binding_id(user_id)
            or type(payload["before_sequence"]) is not int
            or payload["before_sequence"] < 1
        ):
            raise ValueError("cursor inválido")
        return payload["before_sequence"]
    except UnicodeEncodeError, UnicodeDecodeError, binascii.Error, ValueError, TypeError:
        raise _invalid_cursor() from None


def _invalid_cursor() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cursor inválido")


def _binding_id(value: UUID) -> str:
    return hashlib.sha256(value.bytes).hexdigest()


def _public_message(message: ConversationMessage, *, redact: bool) -> SessionMessage:
    content = (
        _REDACTED_CONTENT if redact else minimize_sensitive_memory_input(message.sanitized_content)
    )
    answer: AnswerValue | None = None
    question_id: UUID | None = None
    response: ResponseValue | None = None
    if not redact and message.payload_json:
        try:
            payload = json.loads(message.payload_json)
            if message.kind == MessageKind.USER_INPUT and payload.get("kind") == "answer":
                answer = _ANSWER_ADAPTER.validate_python(payload["answer"])
                question_id = UUID(payload["question_id"])
                if answer.kind == "text":
                    answer = answer.model_copy(
                        update={"text": minimize_sensitive_memory_input(answer.text)}
                    )
                elif answer.kind == "choice":
                    answer = answer.model_copy(
                        update={"option_id": minimize_sensitive_memory_input(answer.option_id)}
                    )
            elif message.role == MessageRole.ASSISTANT:
                result = payload.get("response")
                if isinstance(result, dict):
                    response = _RESPONSE_ADAPTER.validate_python(result)
                    if isinstance(response, QuestionResponse):
                        response = response.model_copy(
                            update={
                                "prompt": minimize_sensitive_memory_input(response.prompt),
                                "options": [
                                    option.model_copy(
                                        update={
                                            "label": minimize_sensitive_memory_input(option.label)
                                        }
                                    )
                                    for option in response.options
                                ],
                            }
                        )
                    elif response.kind == "answer":
                        response = response.model_copy(
                            update={"text": minimize_sensitive_memory_input(response.text)}
                        )
        except AttributeError, KeyError, TypeError, ValueError, ValidationError:
            # Um payload interno/corrompido nunca é propagado ao contrato público.
            answer = None
            question_id = None
            response = None

    return SessionMessage(
        message_id=message.message_id,
        interaction_id=message.interaction_id,
        sequence=message.sequence,
        role=message.role.value,
        kind=message.kind.value,
        content=content,
        created_at=message.created_at,
        question_id=question_id,
        answer=answer,
        response=response,
        redacted=redact,
    )
