"""Porta para renderização controlada do relatório e do e-mail."""

from dataclasses import dataclass
from typing import Protocol

from packages.contracts import LotReportData, RecipientRef


@dataclass(frozen=True, slots=True)
class RenderedReport:
    subject: str
    body: str


class ReportRenderer(Protocol):
    def render(
        self,
        report: LotReportData,
        recipients: tuple[RecipientRef, ...],
        title: str,
    ) -> RenderedReport:
        """Renderiza usando template controlado e dados já validados."""
