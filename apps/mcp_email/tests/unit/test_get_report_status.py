from datetime import UTC, datetime
from uuid import uuid4

import pytest
from argos_mcp_email.application import GetReportStatus
from argos_mcp_email.domain.errors import ReportNotFoundError, ReportOwnershipError
from argos_mcp_email.domain.models import DispatchRecord, DispatchState

from packages.contracts import DispatchStatus, ReportStatusRequest


class FakeDispatchRepository:
    def __init__(self, operation):
        self.operation = operation

    async def get(self, operation_id):
        if self.operation and self.operation.operation_id == operation_id:
            return self.operation
        return None


def operation():
    now = datetime.now(UTC)
    return DispatchRecord(
        operation_id=uuid4(),
        report_id=uuid4(),
        requested_by=uuid4(),
        idempotency_key="send-1",
        state=DispatchState.UNKNOWN,
        provider_message_id=None,
        error_code="provider_result_unknown",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_returns_unknown_status_for_owner():
    current = operation()
    result = await GetReportStatus(dispatch_repository=FakeDispatchRepository(current)).execute(
        ReportStatusRequest(operation_id=current.operation_id, requested_by=current.requested_by)
    )
    assert result.status == DispatchStatus.UNKNOWN
    assert result.error_code == "provider_result_unknown"


@pytest.mark.asyncio
async def test_rejects_status_lookup_for_another_user():
    current = operation()
    with pytest.raises(ReportOwnershipError):
        await GetReportStatus(dispatch_repository=FakeDispatchRepository(current)).execute(
            ReportStatusRequest(operation_id=current.operation_id, requested_by=uuid4())
        )


@pytest.mark.asyncio
async def test_rejects_unknown_operation():
    with pytest.raises(ReportNotFoundError):
        await GetReportStatus(dispatch_repository=FakeDispatchRepository(None)).execute(
            ReportStatusRequest(operation_id=uuid4(), requested_by=uuid4())
        )
