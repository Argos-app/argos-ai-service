from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_email.domain import (
    DispatchRecord,
    DispatchState,
    IdempotencyConflictError,
    InvalidDispatchTransitionError,
    PreparedReportRecord,
    ReportExpiredError,
    ResolvedRecipient,
)
from argos_mcp_email.domain.rules import (
    ensure_dispatch_transition,
    ensure_idempotency_consistency,
    ensure_report_usable,
)


def report(*, expires_at: datetime | None = None) -> PreparedReportRecord:
    now = datetime.now(UTC)
    return PreparedReportRecord(
        report_id=uuid4(),
        requested_by=uuid4(),
        recipients=(ResolvedRecipient(uuid4(), "Matheus Fazan", "matheus@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        source_payload_hash="sha256:abc",
        created_at=now,
        expires_at=expires_at or now + timedelta(hours=1),
    )


def test_report_must_be_valid_for_the_requester_and_within_validity() -> None:
    prepared = report()

    ensure_report_usable(prepared, requested_by=prepared.requested_by)

    with pytest.raises(ReportExpiredError):
        ensure_report_usable(
            prepared,
            requested_by=prepared.requested_by,
            now=prepared.expires_at,
        )


def test_idempotency_cannot_change_report_or_requester() -> None:
    prepared = report()
    operation = DispatchRecord(
        operation_id=uuid4(),
        report_id=prepared.report_id,
        requested_by=prepared.requested_by,
        idempotency_key="send-1",
        state=DispatchState.PENDING,
        provider_message_id=None,
        created_at=prepared.created_at,
        updated_at=prepared.created_at,
    )

    ensure_idempotency_consistency(
        operation,
        report_id=prepared.report_id,
        requested_by=prepared.requested_by,
    )

    with pytest.raises(IdempotencyConflictError):
        ensure_idempotency_consistency(
            operation,
            report_id=uuid4(),
            requested_by=prepared.requested_by,
        )


def test_unknown_dispatch_is_terminal_until_reconciled() -> None:
    ensure_dispatch_transition(DispatchState.PENDING, DispatchState.UNKNOWN)

    with pytest.raises(InvalidDispatchTransitionError):
        ensure_dispatch_transition(DispatchState.UNKNOWN, DispatchState.ACCEPTED)
