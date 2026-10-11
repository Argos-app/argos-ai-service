"""Modelos de domínio para conversas persistidas no serviço principal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.common import require_utc


class InteractionKind(StrEnum):
    MESSAGE = "message"
    ANSWER = "answer"


class InteractionStatus(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageKind(StrEnum):
    USER_INPUT = "user_input"
    ASSISTANT_ANSWER = "assistant_answer"
    CLARIFICATION_QUESTION = "clarification_question"


class ExecutionTarget(StrEnum):
    AGENT = "agent"
    TOOL = "tool"


class ExecutionStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExecutionMessageRole(StrEnum):
    ASSISTANT_TOOL_CALL = "assistant_tool_call"
    TOOL_RESPONSE = "tool_response"
    AGENT_RESPONSE = "agent_response"


@dataclass(frozen=True, slots=True)
class ProcessingLease:
    """Posse temporária do processamento de uma sessão pelo main_api."""

    owner_id: UUID
    fencing_token: int
    acquired_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.fencing_token < 1:
            raise ValueError("fencing_token deve ser positivo")
        require_utc(self.acquired_at, "acquired_at")
        require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.acquired_at:
            raise ValueError("expires_at deve ser posterior a acquired_at")


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """Propriedade e estado durável de uma sessão; não tem expiração automática."""

    session_id: UUID
    user_id: UUID
    farm_ids: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime
    user_message_count: int = 0
    next_message_sequence: int = 1
    version: int = 1
    processing_lease: ProcessingLease | None = None

    def __post_init__(self) -> None:
        require_utc(self.created_at, "created_at")
        require_utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at não pode anteceder created_at")
        if not self.farm_ids or len(self.farm_ids) != len(set(self.farm_ids)):
            raise ValueError("farm_ids deve conter fazendas autorizadas, sem duplicatas")
        if self.user_message_count < 0:
            raise ValueError("user_message_count não pode ser negativo")
        if self.next_message_sequence < 1:
            raise ValueError("next_message_sequence deve ser positivo")
        if self.version < 1:
            raise ValueError("version deve ser positiva")


@dataclass(frozen=True, slots=True)
class InteractionRecord:
    """Entrada sanitizada e resultado público de uma requisição na sessão."""

    interaction_id: UUID
    session_id: UUID
    user_id: UUID
    deduplication_key: str
    kind: InteractionKind
    sanitized_input: str
    input_hash: str
    status: InteractionStatus
    created_at: datetime
    updated_at: datetime
    result_json: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        require_utc(self.created_at, "created_at")
        require_utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at não pode anteceder created_at")
        if not self.sanitized_input.strip():
            raise ValueError("sanitized_input não pode estar vazio")
        if not self.deduplication_key.strip():
            raise ValueError("deduplication_key não pode estar vazio")
        if not self.input_hash.strip():
            raise ValueError("input_hash não pode estar vazio")
        if self.version < 1:
            raise ValueError("version deve ser positiva")
        if self.status == InteractionStatus.COMPLETED and self.result_json is None:
            raise ValueError("interação concluída exige resultado público")


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """Mensagem pública da conversa, armazenada após sanitização."""

    message_id: UUID
    session_id: UUID
    interaction_id: UUID
    sequence: int
    role: MessageRole
    kind: MessageKind
    sanitized_content: str
    created_at: datetime
    user_message_number: int | None = None
    payload_json: str | None = None

    def __post_init__(self) -> None:
        require_utc(self.created_at, "created_at")
        if self.sequence < 1:
            raise ValueError("sequence deve ser positiva")
        if not self.sanitized_content.strip():
            raise ValueError("sanitized_content não pode estar vazio")
        if self.user_message_number is not None and self.user_message_number < 1:
            raise ValueError("user_message_number deve ser positivo")
        if self.role == MessageRole.USER and self.user_message_number is None:
            raise ValueError("mensagem do usuário exige user_message_number")
        if self.role == MessageRole.ASSISTANT and self.user_message_number is not None:
            raise ValueError("mensagem do assistente não incrementa o contador do usuário")


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """Registro sanitizado de uma chamada real de agente ou ferramenta."""

    event_id: UUID
    invocation_id: UUID
    session_id: UUID
    interaction_id: UUID
    target: ExecutionTarget
    target_name: str
    status: ExecutionStatus
    created_at: datetime
    attempt: int = 1
    sanitized_input: str | None = None
    sanitized_output: str | None = None
    messages: tuple[ExecutionMessage, ...] = ()
    safe_error_code: str | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_utc(self.created_at, "created_at")
        if self.completed_at is not None:
            require_utc(self.completed_at, "completed_at")
            if self.completed_at < self.created_at:
                raise ValueError("completed_at não pode anteceder created_at")
        if not self.target_name.strip():
            raise ValueError("target_name não pode estar vazio")
        if self.attempt < 1:
            raise ValueError("attempt deve ser positivo")
        if self.status == ExecutionStatus.STARTED and self.completed_at is not None:
            raise ValueError("chamada iniciada não pode ter completed_at")
        if self.status != ExecutionStatus.STARTED and self.completed_at is None:
            raise ValueError("chamada concluída exige completed_at")
        if self.status == ExecutionStatus.FAILED and not self.safe_error_code:
            raise ValueError("falha exige um código seguro")


@dataclass(frozen=True, slots=True)
class ExecutionMessage:
    """Mensagem sanitizada de chamada/resposta de tool, sem raciocínio privado."""

    role: ExecutionMessageRole
    sanitized_content: str
    tool_name: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not self.sanitized_content.strip():
            raise ValueError("sanitized_content da execução não pode estar vazio")
        if (
            self.role
            in (
                ExecutionMessageRole.ASSISTANT_TOOL_CALL,
                ExecutionMessageRole.TOOL_RESPONSE,
            )
            and not self.tool_name
        ):
            raise ValueError("mensagem de tool exige tool_name")
