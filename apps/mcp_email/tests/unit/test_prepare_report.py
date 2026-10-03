from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from argos_mcp_email.application import PrepareReport
from argos_mcp_email.ports.report_renderer import RenderedReport

from packages.contracts import (
    LotReportData,
    LotReportEnvelope,
    LotReportIdentity,
    PreparedReportStatus,
    PrepareReportRequest,
    RecipientRef,
    RecipientResolution,
    RecipientResolutionStatus,
    RecipientSelector,
)

REQUESTER = UUID("00000000-0000-4000-8000-000000000001")
LOT_ID = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def source() -> LotReportEnvelope:
    return LotReportEnvelope(
        report=LotReportData(
            requested_lot=LotReportIdentity(
                lot_id=LOT_ID,
                lot_code="BOI-FELIZ",
                lot_category="ENGORDA",
                lot_active=True,
                lot_is_exception=False,
                lot_head_count=20,
                lot_open_date=date(2026, 1, 1),
                fit_for_slaughter=True,
                herd_id=uuid4(),
                herd_name="Rebanho Feliz",
                herd_breed="Nelore",
                property_id=uuid4(),
                property_name="Fazenda Argos",
            ),
            queried_at=NOW,
        ),
        principal_user_id=REQUESTER,
        authorized_property_ids=(uuid4(),),
        payload_hash="sha256:lot-report",
        signature="signature",
        signature_key_id="mcp-data-key-1",
    )


class FakeSourceVerifier:
    async def verify(self, value):
        return value


class FakeRecipientDirectory:
    def __init__(self, resolutions):
        self.resolutions = resolutions

    async def resolve(self, selectors):
        return self.resolutions


class FakeRenderer:
    def render(self, *, report, recipients, title):
        return RenderedReport(subject=title, body=f"{report.requested_lot.lot_code}: pronto")


class FakeReportRepository:
    def __init__(self):
        self.saved = []

    async def save(self, report):
        self.saved.append(report)


def prepare(directory):
    repository = FakeReportRepository()
    use_case = PrepareReport(
        source_verifier=FakeSourceVerifier(),
        recipient_directory=directory,
        renderer=FakeRenderer(),
        report_repository=repository,
        clock=lambda: NOW,
        id_factory=lambda: LOT_ID,
    )
    return use_case, repository


@pytest.mark.asyncio
async def test_prepares_and_persists_report_when_all_recipients_resolve():
    recipient = RecipientRef(
        recipient_id=uuid4(), name="Matheus Fazan", email="matheus@example.com"
    )
    use_case, repository = prepare(
        FakeRecipientDirectory(
            [
                RecipientResolution(
                    selector=RecipientSelector(kind="name", value="Matheus Fazan"),
                    status=RecipientResolutionStatus.RESOLVED,
                    matches=(recipient,),
                )
            ]
        )
    )

    result = await use_case.execute(
        PrepareReportRequest(
            report_source=source(),
            recipients=(RecipientSelector(kind="name", value="Matheus Fazan"),),
            report_title="Relatório do lote Boi Feliz",
            requested_by=REQUESTER,
        )
    )

    assert result.status == PreparedReportStatus.PREPARED
    assert result.body == "BOI-FELIZ: pronto"
    assert len(repository.saved) == 1


@pytest.mark.asyncio
async def test_returns_pending_recipients_without_rendering_or_persisting():
    use_case, repository = prepare(
        FakeRecipientDirectory(
            [
                RecipientResolution(
                    selector=RecipientSelector(kind="name", value="Matheus Fazan"),
                    status=RecipientResolutionStatus.AMBIGUOUS,
                    matches=(
                        RecipientRef(
                            recipient_id=uuid4(),
                            name="Matheus Fazan",
                            email="one@example.com",
                        ),
                        RecipientRef(
                            recipient_id=uuid4(),
                            name="Matheus Fazan",
                            email="two@example.com",
                        ),
                    ),
                )
            ]
        )
    )

    result = await use_case.execute(
        PrepareReportRequest(
            report_source=source(),
            recipients=(RecipientSelector(kind="name", value="Matheus Fazan"),),
            report_title="Relatório do lote Boi Feliz",
            requested_by=REQUESTER,
        )
    )

    assert result.status == PreparedReportStatus.NEEDS_RECIPIENTS
    assert result.unresolved[0].status == RecipientResolutionStatus.AMBIGUOUS
    assert repository.saved == []
