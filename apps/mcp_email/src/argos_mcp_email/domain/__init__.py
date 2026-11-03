"""Núcleo de regras do MCP de e-mail."""

from .errors import (
    DomainError,
    IdempotencyConflictError,
    InvalidDispatchTransitionError,
    RecipientDirectoryError,
    ReportAlreadyExistsError,
    ReportExpiredError,
    ReportNotFoundError,
    ReportOwnershipError,
    SourceVerificationError,
    UnresolvedRecipientsError,
)
from .models import (
    DispatchRecord,
    DispatchState,
    PreparedReportRecord,
    ReportAttachmentRecord,
    ReportState,
    ResolvedRecipient,
)

__all__ = [
    "DispatchRecord",
    "DispatchState",
    "DomainError",
    "IdempotencyConflictError",
    "InvalidDispatchTransitionError",
    "PreparedReportRecord",
    "ReportAttachmentRecord",
    "RecipientDirectoryError",
    "ReportState",
    "ReportAlreadyExistsError",
    "ReportExpiredError",
    "ReportNotFoundError",
    "ReportOwnershipError",
    "ResolvedRecipient",
    "SourceVerificationError",
    "UnresolvedRecipientsError",
]
