from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_email.adapters.storage import InMemoryReportRepository
from argos_mcp_email.domain import (
    PreparedReportRecord,
    ReportAlreadyExistsError,
    ResolvedRecipient,
)


def report() -> PreparedReportRecord:
    now = datetime.now(UTC)
    return PreparedReportRecord(
        report_id=uuid4(),
        requested_by=uuid4(),
        recipients=(ResolvedRecipient(uuid4(), "Matheus Fazan", "matheus@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        source_payload_hash="sha256:abc",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_saves_and_recovers_prepared_report() -> None:
    repository = InMemoryReportRepository()
    prepared = report()

    await repository.save(prepared)

    assert await repository.get(prepared.report_id) == prepared
    assert await repository.count() == 1


@pytest.mark.asyncio
async def test_rejects_duplicate_report_id() -> None:
    repository = InMemoryReportRepository()
    prepared = report()
    await repository.save(prepared)

    with pytest.raises(ReportAlreadyExistsError):
        await repository.save(prepared)
