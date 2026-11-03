from datetime import UTC, date, datetime
from uuid import uuid4

from argos_mcp_email.adapters.rendering import TemplateReportRenderer

from packages.contracts import (
    ApplicationRecord,
    LotReportData,
    LotReportIdentity,
    RecipientRef,
)


def test_renderer_fills_controlled_lot_template() -> None:
    report = LotReportData(
        requested_lot=LotReportIdentity(
            lot_id=uuid4(),
            lot_code="BOI-FELIZ",
            lot_category="ENGORDA",
            lot_active=True,
            lot_is_exception=False,
            lot_head_count=24,
            lot_open_date=date(2026, 1, 1),
            fit_for_slaughter=True,
            herd_id=uuid4(),
            herd_name="Rebanho Feliz",
            herd_breed="Nelore",
            property_id=uuid4(),
            property_name="Fazenda Argos",
        ),
        applications=(
            ApplicationRecord(
                application_id=uuid4(),
                application_date=date(2026, 9, 1),
                medicine_name="Vacina Teste",
                active_ingredient="Ingrediente",
                medicine_batch_number="MED-001",
                dose="2",
                unit="ml",
                route="INTRAMUSCULAR",
                head_count_applied=10,
                meat_withdrawal_end=date(2026, 9, 11),
                active=True,
            ),
        ),
        queried_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
    )

    result = TemplateReportRenderer().render(
        report,
        (RecipientRef(recipient_id=uuid4(), name="Matheus Fazan", email="m@example.com"),),
        "Relatório sanitário",
    )

    assert result.subject == "Relatório sanitário — lote BOI-FELIZ"
    assert "Fazenda: Fazenda Argos" in result.body
    assert "Vacina Teste" in result.body
    assert "MED-001" in result.body
