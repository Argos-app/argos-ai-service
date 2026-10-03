"""Núcleo de regras do MCP de e-mail."""

from .errors import DomainError
from .models import DispatchRecord, DispatchState, PreparedReportRecord, ReportState

__all__ = [
    "DispatchRecord",
    "DispatchState",
    "DomainError",
    "PreparedReportRecord",
    "ReportState",
]
