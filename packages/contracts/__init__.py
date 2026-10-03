"""Contratos compartilhados entre as aplicações do Argos."""

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
]
