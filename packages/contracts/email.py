"""Contratos compartilhados para preparação, envio e acompanhamento de relatórios."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .lot_report import LotReportEnvelope


class _EmailContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class RecipientSelector(_EmailContract):
    """Identificador informado pelo usuário para resolver um destinatário."""

    kind: Literal["name", "email"]
    value: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_email_selector(self) -> RecipientSelector:
        if self.kind == "email":
            local, separator, domain = self.value.partition("@")
            if not separator or not local or "." not in domain:
                raise ValueError("seletor de e-mail inválido")
        return self


class RecipientRef(_EmailContract):
    """Destinatário encontrado no cadastro autorizado."""

    recipient_id: UUID
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=120)


class RecipientResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class RecipientResolution(_EmailContract):
    selector: RecipientSelector
    status: RecipientResolutionStatus
    matches: tuple[RecipientRef, ...] = ()

    @model_validator(mode="after")
    def validate_matches(self) -> RecipientResolution:
        if self.status == RecipientResolutionStatus.RESOLVED and len(self.matches) != 1:
            raise ValueError("resolved exige exatamente um destinatário")
        if self.status == RecipientResolutionStatus.AMBIGUOUS and len(self.matches) < 2:
            raise ValueError("ambiguous exige pelo menos duas correspondências")
        if self.status != RecipientResolutionStatus.RESOLVED and self.matches == ():
            return self
        if self.status == RecipientResolutionStatus.NOT_FOUND and self.matches:
            raise ValueError("not_found não pode conter correspondências")
        return self


class PrepareReportRequest(_EmailContract):
    """Entrada da tool de preparação do MCP de e-mail."""

    report_source: LotReportEnvelope
    recipients: tuple[RecipientSelector, ...] = Field(min_length=1)
    report_title: str = Field(min_length=1, max_length=200)
    requested_by: UUID

    @field_validator("report_title")
    @classmethod
    def validate_report_title(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("report_title deve conter uma única linha")
        return value

    @model_validator(mode="after")
    def validate_requester(self) -> PrepareReportRequest:
        if self.requested_by != self.report_source.principal_user_id:
            raise ValueError("requested_by deve corresponder ao usuário do payload")
        return self


class PreparedReportStatus(StrEnum):
    PREPARED = "prepared"
    NEEDS_RECIPIENTS = "needs_recipients"
    REJECTED = "rejected"


class ReportAttachment(_EmailContract):
    """Metadados verificáveis do documento anexado ao relatório preparado."""

    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(min_length=1, max_length=120)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=8_000_000)

    @model_validator(mode="after")
    def validate_filename(self) -> ReportAttachment:
        if "/" in self.filename or "\\" in self.filename or "\x00" in self.filename:
            raise ValueError("filename deve ser um nome simples de arquivo")
        return self


class PreparedReport(_EmailContract):
    """Relatório renderizado e pronto para uma solicitação de envio."""

    report_id: UUID
    status: PreparedReportStatus
    requested_by: UUID
    recipients: tuple[RecipientRef, ...] = ()
    subject: str | None = None
    body: str | None = None
    attachments: tuple[ReportAttachment, ...] = ()
    created_at: datetime
    expires_at: datetime
    unresolved: tuple[RecipientResolution, ...] = ()

    @model_validator(mode="after")
    def validate_prepared_content(self) -> PreparedReport:
        if self.status == PreparedReportStatus.PREPARED:
            if not self.recipients or not self.subject or not self.body:
                raise ValueError("prepared exige destinatários, assunto e corpo")
            if self.unresolved:
                raise ValueError("prepared não pode conter destinatários pendentes")
        else:
            if self.subject or self.body:
                raise ValueError("relatório pendente ou rejeitado não pode ter conteúdo")
        return self


class SendReportRequest(_EmailContract):
    """Entrada da tool que solicita o envio de um relatório preparado."""

    report_id: UUID
    requested_by: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)


class DispatchStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"


class SendReportResult(_EmailContract):
    """Resultado conhecido da solicitação ao provedor de e-mail."""

    operation_id: UUID
    report_id: UUID
    status: DispatchStatus
    provider_message_id: str | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class ReportStatusRequest(_EmailContract):
    operation_id: UUID
    requested_by: UUID


class ReportStatusResult(SendReportResult):
    """Estado retornado para consulta posterior."""
