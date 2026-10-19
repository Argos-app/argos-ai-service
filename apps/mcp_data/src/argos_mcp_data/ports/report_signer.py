"""Porta de assinatura de origens de relatório de lote."""

from typing import Protocol

from argos_mcp_data.domain.access import AuthorizedScope
from packages.contracts import LotReportData, LotReportEnvelope


class LotReportSigner(Protocol):
    def sign(self, report: LotReportData, scope: AuthorizedScope) -> LotReportEnvelope: ...
