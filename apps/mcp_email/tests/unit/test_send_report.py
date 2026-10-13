import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_email.application import SendReport
from argos_mcp_email.domain import (
    DispatchRecord,
    DispatchState,
    PreparedReportRecord,
    ReportNotFoundError,
    ResolvedRecipient,
)
from argos_mcp_email.ports.mail_provider import ProviderSendResult

from packages.contracts import DispatchStatus, SendReportRequest


class FakeReportRepository:
    def __init__(self, report=None):
        self.report = report

    async def get(self, report_id):
        return self.report if self.report and self.report.report_id == report_id else None


class FakeDispatchRepository:
    def __init__(self, existing=None):
        self.existing = existing
        self.created = []
        self.updated = []
        self.send_claimed = False

    async def find_by_idempotency_key(self, key):
        return self.existing if self.existing and self.existing.idempotency_key == key else None

    async def create_pending(self, *, operation_id, report_id, requested_by, idempotency_key):
        record = DispatchRecord(
            operation_id=operation_id,
            report_id=report_id,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            state=DispatchState.PENDING,
            provider_message_id=None,
            error_code=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.created.append(record)
        self.existing = record
        return record

    async def claim_for_send(self, operation_id):
        if self.send_claimed:
            return False
        self.send_claimed = True
        return True

    async def update_state(self, operation_id, state, *, provider_message_id=None, error_code=None):
        current = self.created[-1]
        updated = replace(
            current,
            state=state,
            provider_message_id=provider_message_id,
            error_code=error_code,
            updated_at=NOW,
        )
        self.updated.append(updated)
        self.existing = updated
        return updated

    async def get(self, operation_id):
        return self.existing


class FakeProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def send(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


class ConcurrentFakeDispatchRepository(FakeDispatchRepository):
    def __init__(self):
        super().__init__()
        self.initial_reads = 0
        self.both_initial_reads = asyncio.Event()

    async def find_by_idempotency_key(self, key):
        if self.existing is None:
            self.initial_reads += 1
            if self.initial_reads == 2:
                self.both_initial_reads.set()
            await self.both_initial_reads.wait()
        return await super().find_by_idempotency_key(key)


REQUESTER = uuid4()
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def prepared_report():
    return PreparedReportRecord(
        report_id=uuid4(),
        requested_by=REQUESTER,
        recipients=(ResolvedRecipient(uuid4(), "Matheus Fazan", "matheus@example.com"),),
        subject="Relatório do lote",
        body="Conteúdo",
        source_payload_hash="sha256:abc",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def use_case(report, dispatch, provider):
    return SendReport(
        report_repository=FakeReportRepository(report),
        dispatch_repository=dispatch,
        mail_provider=provider,
        sender="no-reply@argos.local",
        id_factory=lambda: uuid4(),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_sends_prepared_report_and_persists_accepted_state():
    report = prepared_report()
    dispatch = FakeDispatchRepository()
    provider = FakeProvider(ProviderSendResult(accepted=True, provider_message_id="provider-1"))

    result = await use_case(report, dispatch, provider).execute(
        SendReportRequest(
            report_id=report.report_id, requested_by=REQUESTER, idempotency_key="send-1"
        )
    )

    assert result.status == DispatchStatus.ACCEPTED
    assert provider.calls[0]["sender"] == "no-reply@argos.local"


@pytest.mark.asyncio
async def test_provider_exception_becomes_unknown_without_retry():
    report = prepared_report()
    dispatch = FakeDispatchRepository()
    provider = FakeProvider(error=TimeoutError())

    result = await use_case(report, dispatch, provider).execute(
        SendReportRequest(
            report_id=report.report_id, requested_by=REQUESTER, idempotency_key="send-2"
        )
    )

    assert result.status == DispatchStatus.UNKNOWN
    assert result.error_code == "provider_result_unknown"


@pytest.mark.parametrize("state", [DispatchState.PENDING, DispatchState.UNKNOWN])
@pytest.mark.asyncio
async def test_replay_of_pending_or_unknown_operation_never_calls_provider(state):
    report = prepared_report()
    existing = DispatchRecord(
        operation_id=uuid4(),
        report_id=report.report_id,
        requested_by=REQUESTER,
        idempotency_key="ambiguous-operation",
        state=state,
        provider_message_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    dispatch = FakeDispatchRepository(existing=existing)
    provider = FakeProvider(ProviderSendResult(accepted=True, provider_message_id="unexpected"))

    result = await use_case(report, dispatch, provider).execute(
        SendReportRequest(
            report_id=report.report_id,
            requested_by=REQUESTER,
            idempotency_key="ambiguous-operation",
        )
    )

    assert result.status == DispatchStatus(state.value)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_missing_report_is_rejected_before_provider_call():
    dispatch = FakeDispatchRepository()
    provider = FakeProvider(ProviderSendResult(accepted=True))

    with pytest.raises(ReportNotFoundError):
        await use_case(None, dispatch, provider).execute(
            SendReportRequest(report_id=uuid4(), requested_by=REQUESTER, idempotency_key="send-3")
        )
    assert provider.calls == []


@pytest.mark.asyncio
async def test_concurrent_requests_only_call_provider_once():
    report = prepared_report()
    dispatch = ConcurrentFakeDispatchRepository()
    provider = FakeProvider(ProviderSendResult(accepted=True, provider_message_id="provider-1"))
    request = SendReportRequest(
        report_id=report.report_id,
        requested_by=REQUESTER,
        idempotency_key="send-concurrent",
    )
    sender = use_case(report, dispatch, provider)

    first, second = await asyncio.gather(sender.execute(request), sender.execute(request))

    assert len(provider.calls) == 1
    assert first.status == second.status == DispatchStatus.ACCEPTED
    assert first.operation_id == second.operation_id
