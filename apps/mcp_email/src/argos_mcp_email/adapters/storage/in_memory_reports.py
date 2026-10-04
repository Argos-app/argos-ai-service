"""Armazenamento local de relatórios preparados.

Este adapter é adequado para desenvolvimento e testes enquanto o armazenamento
técnico persistente do MCP ainda não foi escolhido.
"""

import asyncio
from uuid import UUID

from argos_mcp_email.domain.errors import ReportAlreadyExistsError
from argos_mcp_email.domain.models import PreparedReportRecord


class InMemoryReportRepository:
    """Implementa ``ReportRepository`` com isolamento entre chamadas."""

    def __init__(self) -> None:
        self._reports: dict[UUID, PreparedReportRecord] = {}
        self._lock = asyncio.Lock()

    async def save(self, report: PreparedReportRecord) -> None:
        async with self._lock:
            if report.report_id in self._reports:
                raise ReportAlreadyExistsError("relatório já foi salvo")
            self._reports[report.report_id] = report

    async def get(self, report_id: UUID) -> PreparedReportRecord | None:
        async with self._lock:
            return self._reports.get(report_id)

    async def count(self) -> int:
        async with self._lock:
            return len(self._reports)
