"""Preparações completas de e-mail mantidas fora do contexto do agente."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from packages.contracts import DispatchStatus, PreparedReport, RecipientSelector


@dataclass(frozen=True, slots=True)
class EmailPreparation:
    reference: UUID
    principal_user_id: UUID
    session_id: UUID
    interaction_id: UUID
    report_artifact_ref: UUID
    request_fingerprint: str
    prepared_report: PreparedReport
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PendingRecipientOption:
    option_id: str
    label: str
    selector: RecipientSelector


@dataclass(frozen=True, slots=True)
class PendingRecipientResolution:
    question_id: UUID
    principal_user_id: UUID
    session_id: UUID
    interaction_id: UUID
    report_artifact_ref: UUID
    input_type: str
    reason_code: str
    question_goal: str
    unresolved_selector: RecipientSelector
    options: tuple[PendingRecipientOption, ...]
    expires_at: datetime


class SendAuthorizationOrigin(StrEnum):
    EXPLICIT_REQUEST = "explicit_request"
    CONFIRMED_QUESTION = "confirmed_question"


@dataclass(frozen=True, slots=True)
class EmailSendAuthorization:
    """Autorização imutável reservada para uma preparação e ação do usuário."""

    authorization_id: UUID
    principal_user_id: UUID
    session_id: UUID
    source_interaction_id: UUID
    preparation_reference: UUID
    report_id: UUID
    request_fingerprint: str
    prepared_fingerprint: str
    recipients_fingerprint: str
    origin: SendAuthorizationOrigin
    idempotency_key: str
    authorized_at: datetime
    expires_at: datetime
    question_id: UUID | None = None
    question_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.origin == SendAuthorizationOrigin.CONFIRMED_QUESTION:
            if self.question_id is None or self.question_expires_at is None:
                raise ValueError("autorização confirmada exige pergunta e validade vinculadas")
            if self.question_expires_at <= self.authorized_at:
                raise ValueError("a pergunta de confirmação já expirou")
            if self.expires_at > self.question_expires_at:
                raise ValueError("autorização excede a validade da pergunta")
        elif self.question_id is not None or self.question_expires_at is not None:
            raise ValueError("pedido explícito não pode carregar vínculo de confirmação")
        if self.expires_at <= self.authorized_at:
            raise ValueError("autorização deve expirar após ser criada")


@dataclass(frozen=True, slots=True)
class EmailOperationReference:
    """Referência local e último resultado conhecido da operação canônica do MCP."""

    authorization_id: UUID
    principal_user_id: UUID
    session_id: UUID
    operation_id: UUID
    recorded_at: datetime
    last_known_status: DispatchStatus | None = None
    last_known_updated_at: datetime | None = None


class EmailPreparationNotFoundError(LookupError):
    """Preparação inexistente ou fora do escopo de usuário/sessão."""
