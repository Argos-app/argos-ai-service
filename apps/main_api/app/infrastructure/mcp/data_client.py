"""Adapter tipado das capacidades de lote publicadas pelo MCP de dados."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.mcp.client import McpToolServerClient
from app.infrastructure.mcp.delegation import McpDelegationTokenIssuer
from app.ports.data_client import (
    ReportLotCandidate,
    ReportLotResolution,
)
from packages.contracts import LotReportEnvelope

_MAX_LOT_PAGES = 10
_PAGE_SIZE = 100


class _LotRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    lot_id: UUID
    property_id: UUID
    property_name: str = Field(min_length=1)
    herd_id: UUID
    herd_name: str = Field(min_length=1)
    lot_code: str = Field(min_length=1)


class _LotPage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    items: tuple[_LotRecord, ...]
    page_size: int = Field(ge=1, le=_PAGE_SIZE)
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0, le=10_000)


class McpDataClient:
    """Consulta lotes no escopo delegado e não presume a existência de origem assinada."""

    def __init__(
        self,
        client: McpToolServerClient,
        delegation_issuer: McpDelegationTokenIssuer,
    ) -> None:
        if client.service_name != "data":
            raise ValueError("McpDataClient exige um cliente MCP de dados")
        self._client = client
        self._issuer = delegation_issuer

    async def resolve_report_lot(
        self,
        *,
        lot_code: str,
        firebase_uid: str,
        principal_user_id: UUID,
        authorized_property_ids: Sequence[UUID],
        request_id: UUID,
        herd_id: UUID | None = None,
        property_id: UUID | None = None,
    ) -> ReportLotResolution:
        code = lot_code.strip()
        properties = tuple(dict.fromkeys(UUID(str(value)) for value in authorized_property_ids))
        if not code or len(code) > 20:
            raise ValueError("código do lote deve ter entre 1 e 20 caracteres")
        if not properties:
            raise ValueError("o escopo autorizado deve conter propriedades")
        if property_id is not None and property_id not in properties:
            raise ValueError("propriedade solicitada está fora do escopo autorizado")

        headers = self._issuer.headers_for_call(
            service="data",
            firebase_uid=firebase_uid,
            authorized_property_ids=properties,
            agent="fazenda",
            operation="cattle.read",
            request_id=request_id,
        )
        candidates: list[ReportLotCandidate] = []
        has_more = False
        property_batches = (
            (property_id,)
            if property_id is not None
            else tuple(properties[index : index + 100] for index in range(0, len(properties), 100))
        )
        if property_id is not None:
            property_batches = (property_batches,)
        for property_batch in property_batches:
            offset = 0
            for _page_number in range(_MAX_LOT_PAGES):
                payload: dict[str, Any] = {
                    "lot_code": code,
                    "property_ids": [str(value) for value in property_batch],
                    "page_size": _PAGE_SIZE,
                    "offset": offset,
                }
                if herd_id is not None:
                    payload["herd_id"] = str(herd_id)

                result = await self._client.call_tool(
                    "list_lots",
                    {"payload": payload},
                    request_headers=headers,
                )
                page = _LotPage.model_validate(_response_payload(result))
                for record in page.items:
                    # MCP authorization remains authoritative; this is an additional
                    # fail-closed check before the candidate reaches the agent.
                    if record.property_id not in properties:
                        raise ValueError("MCP retornou lote fora do escopo delegado")
                    if record.lot_code.casefold() == code.casefold():
                        candidates.append(
                            ReportLotCandidate(
                                lot_id=record.lot_id,
                                lot_code=record.lot_code,
                                property_id=record.property_id,
                                property_name=record.property_name,
                                herd_id=record.herd_id,
                                herd_name=record.herd_name,
                            )
                        )
                if not page.has_more:
                    break
                if page.next_offset is None or page.next_offset <= offset:
                    raise ValueError("paginação MCP inválida")
                offset = page.next_offset
            else:
                has_more = True
        return ReportLotResolution(candidates=tuple(candidates), has_more=has_more)

    async def get_lot_report_source(
        self,
        *,
        lot_id: UUID,
        property_id: UUID,
        firebase_uid: str,
        principal_user_id: UUID,
        authorized_property_ids: Sequence[UUID],
        request_id: UUID,
    ) -> LotReportEnvelope:
        properties = tuple(dict.fromkeys(UUID(str(value)) for value in authorized_property_ids))
        if property_id not in properties:
            raise ValueError("propriedade solicitada está fora do escopo autorizado")
        headers = self._issuer.headers_for_call(
            service="data",
            firebase_uid=firebase_uid,
            authorized_property_ids=properties,
            agent="fazenda",
            operation="report.read",
            request_id=request_id,
        )
        result = await self._client.call_tool(
            "get_lot_report_source",
            {
                "payload": {
                    "lot_id": str(lot_id),
                    "property_id": str(property_id),
                }
            },
            request_headers=headers,
        )
        try:
            envelope = LotReportEnvelope.model_validate(_response_payload(result))
        except (TypeError, ValueError) as exc:
            raise ValueError("MCP de dados retornou envelope de relatório inválido") from exc
        if (
            envelope.principal_user_id != principal_user_id
            or envelope.report.requested_lot.lot_id != lot_id
            or envelope.report.requested_lot.property_id != property_id
            or property_id not in envelope.authorized_property_ids
        ):
            raise ValueError("MCP de dados retornou relatório fora da identidade ou escopo")
        return envelope


def _response_payload(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    text_blocks = [
        block.text
        for block in getattr(result, "content", ())
        if isinstance(getattr(block, "text", None), str)
    ]
    if len(text_blocks) != 1:
        raise ValueError("resposta MCP ausente ou ambígua")
    try:
        return json.loads(text_blocks[0])
    except json.JSONDecodeError as exc:
        raise ValueError("resposta MCP não contém JSON válido") from exc


__all__ = ["McpDataClient"]
