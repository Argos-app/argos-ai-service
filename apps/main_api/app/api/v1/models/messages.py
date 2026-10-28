"""Contrato HTTP para consultar mensagens antigas de uma sessão."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_serializer, model_validator

from app.api.v1.models.interactions import AnswerValue, ResponseValue
from app.application.contracts.base import ApplicationModel
from app.session_config import DEFAULT_MESSAGES_PAGE_SIZE, MAX_MESSAGES_PAGE_SIZE


class MessageHistoryQuery(ApplicationModel):
    """Parâmetros validados da consulta; cursor opaco à camada HTTP."""

    limit: int = Field(
        default=DEFAULT_MESSAGES_PAGE_SIZE,
        ge=1,
        le=MAX_MESSAGES_PAGE_SIZE,
        description="Quantidade máxima de mensagens retornadas na página.",
        examples=[DEFAULT_MESSAGES_PAGE_SIZE],
    )
    cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Cursor opaco retornado em next_cursor para continuar a paginação.",
        examples=["cursor-opaco-retornado-pela-api"],
    )


class SessionMessage(ApplicationModel):
    """Mensagem pública, já sanitizada, retornada ao titular da sessão."""

    message_id: UUID = Field(description="Identificador único da mensagem.")
    interaction_id: UUID = Field(description="Identificador da interação associada à mensagem.")
    sequence: int = Field(
        ge=1, description="Posição cronológica da mensagem na sessão.", examples=[1]
    )
    role: Literal["user", "assistant"] = Field(
        description="Autor da mensagem: usuário ou assistente.", examples=["user"]
    )
    kind: Literal["user_input", "assistant_answer", "clarification_question"] = Field(
        description="Tipo da mensagem armazenada.", examples=["user_input"]
    )
    content: str = Field(
        min_length=1,
        max_length=32_000,
        description="Conteúdo público e sanitizado da mensagem.",
        examples=["Quantos animais há no lote 12?"],
    )
    created_at: datetime = Field(
        description="Data e hora de criação em UTC.", examples=["2026-10-27T12:00:00Z"]
    )
    question_id: UUID | None = Field(
        default=None, description="Identificador da pergunta pendente, quando aplicável."
    )
    answer: AnswerValue | None = Field(
        default=None, description="Resposta do usuário vinculada a uma pergunta, quando aplicável."
    )
    response: ResponseValue | None = Field(
        default=None,
        description="Pergunta ou resposta produzida pelo assistente, quando aplicável.",
    )
    redacted: bool = Field(
        default=False, description="Indica se dados sensíveis foram removidos do conteúdo."
    )
    status: Literal["completed", "degraded"] | None = Field(
        default=None, description="Estado da interação concluída, quando aplicável."
    )
    degradation_reasons: list[str] = Field(
        default_factory=list, description="Códigos das limitações da interação, quando houver."
    )

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
        if self.role == "user" and (self.status is not None or self.degradation_reasons):
            raise ValueError("mensagem do usuário não pode conter status da interação")
        return self

    @model_serializer(mode="wrap")
    def omit_user_outcome_metadata(self, handler):
        serialized = handler(self)
        if self.role == "user":
            serialized.pop("status", None)
            serialized.pop("degradation_reasons", None)
        return serialized


class SessionMessagesPage(ApplicationModel):
    """Página cronológica de mensagens anteriores à fronteira do cursor."""

    schema_version: Literal["1"] = Field(default="1", description="Versão do contrato da página.")
    session_id: UUID = Field(description="Identificador da sessão consultada.")
    items: list[SessionMessage] = Field(description="Mensagens desta página em ordem cronológica.")
    next_cursor: str | None = Field(
        description="Cursor opaco para buscar a próxima página; nulo quando não há próxima página."
    )
    has_more: bool = Field(description="Indica se há mais mensagens disponíveis.", examples=[False])

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
