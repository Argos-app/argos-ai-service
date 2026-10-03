"""Modelos de domínio independentes de transporte e infraestrutura."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ReportState(StrEnum):
    PREPARED = "prepared"
    EXPIRED = "expired"
    REJECTED = "rejected"


class DispatchState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResolvedRecipient:
    recipient_id: UUID
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class PreparedReportRecord:
    report_id: UUID
    requested_by: UUID
    recipients: tuple[ResolvedRecipient, ...]
    subject: str
    body: str
    source_payload_hash: str
    created_at: datetime
    expires_at: datetime
    state: ReportState = ReportState.PREPARED


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    operation_id: UUID
    report_id: UUID
    requested_by: UUID
    idempotency_key: str
    state: DispatchState
    provider_message_id: str | None
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
