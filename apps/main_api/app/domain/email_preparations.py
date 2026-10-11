"""Preparações completas de e-mail mantidas fora do contexto do agente."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from packages.contracts import PreparedReport, RecipientSelector


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


class EmailPreparationNotFoundError(LookupError):
    """Preparação inexistente ou fora do escopo de usuário/sessão."""
