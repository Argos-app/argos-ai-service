"""Contratos compartilhados entre as aplicações do Argos."""

from .email import (
    DispatchStatus,
    PreparedReport,
    PreparedReportStatus,
    PrepareReportRequest,
    RecipientRef,
    RecipientResolution,
    RecipientResolutionStatus,
    RecipientSelector,
    ReportStatusRequest,
    ReportStatusResult,
    SendReportRequest,
    SendReportResult,
)
from .lot_report import (
    AnimalExceptionRecord,
    ApplicationRecord,
    LotReportData,
    LotReportEnvelope,
    LotReportIdentity,
)

__all__ = [
    "AnimalExceptionRecord",
    "ApplicationRecord",
    "LotReportData",
    "LotReportEnvelope",
    "LotReportIdentity",
    "DispatchStatus",
    "PrepareReportRequest",
    "PreparedReport",
    "PreparedReportStatus",
    "RecipientRef",
    "RecipientResolution",
    "RecipientResolutionStatus",
    "RecipientSelector",
    "ReportStatusRequest",
    "ReportStatusResult",
    "SendReportRequest",
    "SendReportResult",
]
