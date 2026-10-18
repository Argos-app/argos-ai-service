"""Resolução de lotes e fronteira da origem de relatório do MCP de dados."""

from datetime import UTC, date, datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.agents.tools.farm_reports import (
    build_farm_report_tool,
)
from app.application.report_artifact_service import (
    ReportSourceValidationError,
    validate_report_source,
)
from app.infrastructure.mcp.data_client import McpDataClient
from app.ports.data_client import (
    DataReportSourceUnavailable,
    ReportLotCandidate,
    ReportLotResolution,
)

from packages.contracts import LotReportData, LotReportEnvelope, LotReportIdentity


class _FakeMcpClient:
    service_name = "data"

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    async def call_tool(self, name, arguments, *, request_headers=None):
        self.calls.append({"name": name, "arguments": arguments, "headers": request_headers})
        return SimpleNamespace(structuredContent=self.pages.pop(0), content=[])


class _FakeIssuer:
    def __init__(self):
        self.calls = []

    def headers_for_call(self, **claims):
        self.calls.append(claims)
        return {"X-Argos-Delegation": "delegated"}


def _page(*items, has_more=False, next_offset=None):
    return {
        "items": list(items),
        "page_size": 100,
        "has_more": has_more,
        "next_offset": next_offset,
    }


def _lot(*, code="L-1", property_id=None, lot_id=None, herd_id=None):
    return {
        "lot_id": str(lot_id or uuid4()),
        "property_id": str(property_id or uuid4()),
        "property_name": "Fazenda A",
        "herd_id": str(herd_id or uuid4()),
        "herd_name": "Rebanho 1",
        "lot_code": code,
        "category": "ENGORDA",
        "active": True,
    }


def _signed_envelope(user_id, property_id, lot_id):
    report = LotReportData(
        requested_lot=LotReportIdentity(
            lot_id=lot_id,
            lot_code="L-1",
            lot_category="ENGORDA",
            lot_active=True,
            lot_is_exception=False,
            lot_exception_reason=None,
            lot_head_count=12,
            lot_open_date=date(2026, 1, 1),
            lot_close_date=None,
            slaughter_release_date=None,
            fit_for_slaughter=False,
            herd_id=uuid4(),
            herd_name="Rebanho A",
            herd_breed="Nelore",
            property_id=property_id,
            property_name="Fazenda A",
        ),
        queried_at=datetime(2026, 10, 11, tzinfo=UTC),
    )
    envelope = LotReportEnvelope(
        report=report,
        principal_user_id=user_id,
        authorized_property_ids=(property_id,),
        valid_until=datetime(2026, 10, 12, tzinfo=UTC),
        payload_hash="pending",
        signature="test-signature",
        signature_key_id="test-key",
    )
    payload = envelope.model_dump_json(exclude={"payload_hash", "signature"}).encode("utf-8")
    return envelope.model_copy(update={"payload_hash": f"sha256:{sha256(payload).hexdigest()}"})


def test_report_source_validation_checks_identity_scope_expiry_and_hash():
    user_id, property_id, lot_id = uuid4(), uuid4(), uuid4()
    envelope = _signed_envelope(user_id, property_id, lot_id)
    valid_now = datetime(2026, 10, 11, tzinfo=UTC)

    assert (
        validate_report_source(
            envelope,
            principal_user_id=user_id,
            lot_id=lot_id,
            authorized_property_ids=(property_id,),
            now=valid_now,
        )
        is envelope
    )

    with pytest.raises(ReportSourceValidationError, match="solicitante"):
        validate_report_source(
            envelope,
            principal_user_id=uuid4(),
            lot_id=lot_id,
            authorized_property_ids=(property_id,),
            now=valid_now,
        )
    with pytest.raises(ReportSourceValidationError, match="lote selecionado"):
        validate_report_source(
            envelope,
            principal_user_id=user_id,
            lot_id=uuid4(),
            authorized_property_ids=(property_id,),
            now=valid_now,
        )
    with pytest.raises(ReportSourceValidationError, match="propriedade"):
        validate_report_source(
            envelope,
            principal_user_id=user_id,
            lot_id=lot_id,
            authorized_property_ids=(uuid4(),),
            now=valid_now,
        )
    with pytest.raises(ReportSourceValidationError, match="expirado"):
        validate_report_source(
            envelope,
            principal_user_id=user_id,
            lot_id=lot_id,
            authorized_property_ids=(property_id,),
            now=datetime(2026, 10, 13, tzinfo=UTC),
        )
    altered = envelope.model_copy(update={"payload_hash": "sha256:invalid"})
    with pytest.raises(ReportSourceValidationError, match="hash"):
        validate_report_source(
            altered,
            principal_user_id=user_id,
            lot_id=lot_id,
            authorized_property_ids=(property_id,),
            now=valid_now,
        )


@pytest.mark.asyncio
async def test_resolve_report_lot_passes_delegated_scope_and_matches_exact_code():
    user_id, property_id, request_id = uuid4(), uuid4(), uuid4()
    lot_id = uuid4()
    lot = _lot(code="l-1", property_id=property_id, lot_id=lot_id)
    client = _FakeMcpClient([_page(lot, _lot(code="L-10", property_id=property_id))])
    issuer = _FakeIssuer()
    data = McpDataClient(client, issuer)

    result = await data.resolve_report_lot(
        lot_code="L-1",
        principal_user_id=user_id,
        authorized_property_ids=(property_id,),
        request_id=request_id,
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].lot_id == lot_id
    assert result.unique_candidate is not None
    assert client.calls[0]["name"] == "list_lots"
    assert client.calls[0]["arguments"]["payload"]["property_ids"] == [str(property_id)]
    assert client.calls[0]["headers"] == {"X-Argos-Delegation": "delegated"}
    assert issuer.calls[0]["operation"] == "cattle.read"
    assert issuer.calls[0]["agent"] == "fazenda"


@pytest.mark.asyncio
async def test_resolve_report_lot_pages_and_batches_property_scope():
    properties = tuple(uuid4() for _ in range(101))
    lot_a = _lot(code="L-1", property_id=properties[0])
    lot_b = _lot(code="L-1", property_id=properties[100])
    client = _FakeMcpClient(
        [
            _page(has_more=True, next_offset=100),
            _page(lot_a),
            _page(lot_b),
        ]
    )
    data = McpDataClient(client, _FakeIssuer())

    result = await data.resolve_report_lot(
        lot_code="L-1",
        principal_user_id=uuid4(),
        authorized_property_ids=properties,
        request_id=uuid4(),
    )

    assert len(result.candidates) == 2
    assert result.unique_candidate is None
    assert [len(call["arguments"]["payload"]["property_ids"]) for call in client.calls] == [
        100,
        100,
        1,
    ]


@pytest.mark.asyncio
async def test_resolve_report_lot_rejects_property_outside_delegated_scope():
    client = _FakeMcpClient([])
    data = McpDataClient(client, _FakeIssuer())

    with pytest.raises(ValueError, match="fora do escopo"):
        await data.resolve_report_lot(
            lot_code="L-1",
            principal_user_id=uuid4(),
            authorized_property_ids=(uuid4(),),
            request_id=uuid4(),
            property_id=uuid4(),
        )

    assert client.calls == []


@pytest.mark.asyncio
async def test_report_tool_returns_candidates_then_fails_closed_without_signed_source():
    class FakeDataClient:
        source_calls = 0

        async def resolve_report_lot(self, **kwargs):
            return ReportLotResolution(
                candidates=(
                    ReportLotCandidate(
                        lot_id=uuid4(),
                        lot_code="L-1",
                        property_id=property_id,
                        property_name="Fazenda A",
                        herd_id=uuid4(),
                        herd_name="Rebanho A",
                    ),
                    ReportLotCandidate(
                        lot_id=uuid4(),
                        lot_code="L-1",
                        property_id=property_id,
                        property_name="Fazenda A",
                        herd_id=uuid4(),
                        herd_name="Rebanho B",
                    ),
                )
            )

        async def get_lot_report_source(self, **kwargs):
            self.source_calls += 1
            raise DataReportSourceUnavailable()

    property_id = uuid4()
    data_client = FakeDataClient()
    tool = build_farm_report_tool(data_client)
    config = {
        "configurable": {
            "argos_user_id": str(uuid4()),
            "argos_request_id": str(uuid4()),
            "argos_authorized_property_ids": (str(property_id),),
            "session_id": str(uuid4()),
            "interaction_id": str(uuid4()),
            "argos_task_id": "farm-task",
        }
    }

    ambiguous = await tool.ainvoke({"lot_code": "L-1"}, config=config)
    assert '"needs_clarification"' in ambiguous
    assert "Rebanho A" in ambiguous and "Rebanho B" in ambiguous
    assert data_client.source_calls == 0

    async def unique_resolution(**kwargs):
        return ReportLotResolution(
            candidates=(
                ReportLotCandidate(
                    lot_id=uuid4(),
                    lot_code="L-1",
                    property_id=property_id,
                    property_name="Fazenda A",
                    herd_id=uuid4(),
                    herd_name="Rebanho A",
                ),
            )
        )

    data_client.resolve_report_lot = unique_resolution
    unavailable = await tool.ainvoke({"lot_code": "L-1"}, config=config)
    assert '"report_source_unavailable"' in unavailable
    assert data_client.source_calls == 1
