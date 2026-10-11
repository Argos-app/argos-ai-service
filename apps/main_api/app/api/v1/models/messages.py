"""Contrato HTTP para consultar mensagens antigas de uma sessão."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.api.v1.models.base import StrictModel
from app.api.v1.models.interactions import AnswerValue, ResponseValue
from app.session_config import DEFAULT_MESSAGES_PAGE_SIZE, MAX_MESSAGES_PAGE_SIZE


class MessageHistoryQuery(StrictModel):
    """Parâmetros validados da consulta; cursor opaco à camada HTTP."""

    limit: int = Field(
        default=DEFAULT_MESSAGES_PAGE_SIZE,
        ge=1,
        le=MAX_MESSAGES_PAGE_SIZE,
    )
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)


class SessionMessage(StrictModel):
    """Mensagem pública, já sanitizada, retornada ao titular da sessão."""

    message_id: UUID
    interaction_id: UUID
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant"]
    kind: Literal["user_input", "assistant_answer", "clarification_question"]
    content: str = Field(min_length=1, max_length=32_000)
    created_at: datetime
    question_id: UUID | None = None
    answer: AnswerValue | None = None
    response: ResponseValue | None = None
    redacted: bool = False

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if (
            value.tzinfo is None
            or value.utcoffset() is None
            or value.utcoffset().total_seconds() != 0
        ):
            raise ValueError("created_at deve usar UTC")
        return value

    @model_validator(mode="after")
    def validate_role_and_kind(self) -> SessionMessage:
        if self.kind == "user_input" and self.role != "user":
            raise ValueError("user_input deve pertencer ao usuário")
        if self.kind != "user_input" and self.role != "assistant":
            raise ValueError("resposta e pergunta de esclarecimento devem pertencer ao assistente")
        if self.role == "user" and self.response is not None:
            raise ValueError("mensagem do usuário não pode conter response")
        if self.role == "assistant" and (self.question_id is not None or self.answer is not None):
            raise ValueError("mensagem do assistente não pode conter resposta do usuário")
        return self


class SessionMessagesPage(StrictModel):
    """Página cronológica de mensagens anteriores à fronteira do cursor."""

    schema_version: Literal["1"] = "1"
    session_id: UUID
    items: list[SessionMessage]
    next_cursor: str | None
    has_more: bool

    @model_validator(mode="after")
    def validate_page(self) -> SessionMessagesPage:
        if len(self.items) > MAX_MESSAGES_PAGE_SIZE:
            raise ValueError(f"items não pode exceder {MAX_MESSAGES_PAGE_SIZE} mensagens")
        sequences = [message.sequence for message in self.items]
        if sequences != sorted(set(sequences)):
            raise ValueError("items deve ter sequências únicas em ordem crescente")
        if self.has_more and not self.next_cursor:
            raise ValueError("next_cursor é obrigatório quando has_more é true")
        if self.next_cursor is not None and not self.items:
            raise ValueError("página vazia não pode avançar next_cursor")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("next_cursor deve ser nulo quando has_more é false")
        return self
