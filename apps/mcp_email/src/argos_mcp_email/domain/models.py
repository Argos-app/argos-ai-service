"""Modelos de domínio independentes de transporte e infraestrutura."""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID


class ReportState(StrEnum):
    PREPARED = "prepared"
    EXPIRED = "expired"
    REJECTED = "rejected"


class DispatchState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResolvedRecipient:
    recipient_id: UUID
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class ReportAttachmentRecord:
    filename: str
    content_type: str
    content: bytes
    sha256: str

    def __post_init__(self) -> None:
        if (
            not self.filename
            or "/" in self.filename
            or "\\" in self.filename
            or "\x00" in self.filename
        ):
            raise ValueError("filename do anexo deve ser um nome simples")
        if not isinstance(self.content_type, str) or not re.fullmatch(
            r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", self.content_type
        ):
            raise ValueError("content_type do anexo deve ser MIME")
        if not isinstance(self.content, bytes) or not 0 < len(self.content) <= 8_000_000:
            raise ValueError("conteúdo do anexo deve ter até 8 MB")
        if self.sha256 != sha256(self.content).hexdigest():
            raise ValueError("sha256 do anexo não corresponde ao conteúdo")


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
    attachments: tuple[ReportAttachmentRecord, ...] = ()
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
