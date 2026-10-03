"""Porta para armazenar e recuperar relatórios preparados."""

from typing import Protocol
from uuid import UUID

from argos_mcp_email.domain.models import PreparedReportRecord


class ReportRepository(Protocol):
    async def save(self, report: PreparedReportRecord) -> None:
        """Persiste uma preparação de relatório."""

    async def get(self, report_id: UUID) -> PreparedReportRecord | None:
        """Recupera uma preparação pelo identificador."""
