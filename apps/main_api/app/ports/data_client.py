"""Porta de leitura de dados autorizados para relatórios de lote."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from packages.contracts import LotReportEnvelope


@dataclass(frozen=True, slots=True)
class ReportLotCandidate:
    lot_id: UUID
    lot_code: str
    property_id: UUID
    property_name: str
    herd_id: UUID
    herd_name: str


@dataclass(frozen=True, slots=True)
class ReportLotResolution:
    """Resultado de busca exata, sem selecionar homônimos implicitamente."""

    candidates: tuple[ReportLotCandidate, ...]
    has_more: bool = False

    @property
    def unique_candidate(self) -> ReportLotCandidate | None:
        if len(self.candidates) == 1 and not self.has_more:
            return self.candidates[0]
        return None


class DataReportSourceUnavailable(RuntimeError):
    """O produtor MCP ainda não publica o envelope assinado de relatório."""


class DataClient(Protocol):
    async def resolve_report_lot(
        self,
        *,
        lot_code: str,
        principal_user_id: UUID,
        authorized_property_ids: Sequence[UUID],
        request_id: UUID,
        herd_id: UUID | None = None,
        property_id: UUID | None = None,
    ) -> ReportLotResolution: ...

    async def get_lot_report_source(
        self,
        *,
        lot_id: UUID,
        property_id: UUID,
        principal_user_id: UUID,
        authorized_property_ids: Sequence[UUID],
        request_id: UUID,
    ) -> LotReportEnvelope: ...
