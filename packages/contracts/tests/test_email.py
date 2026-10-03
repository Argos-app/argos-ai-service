from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.email import (
    PreparedReport,
    PreparedReportStatus,
    RecipientRef,
    RecipientSelector,
)


def test_email_selector_requires_an_email_shape() -> None:
    with pytest.raises(ValidationError):
        RecipientSelector(kind="email", value="matheus")


def test_prepared_report_requires_rendered_content() -> None:
    now = datetime.now(UTC)
    recipient = RecipientRef(
        recipient_id=uuid4(),
        name="Matheus Fazan",
        email="matheus@example.com",
    )

    report = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=uuid4(),
        recipients=(recipient,),
        subject="Relatório do lote",
        body="Conteúdo do relatório",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )

    assert report.recipients[0].email == "matheus@example.com"
