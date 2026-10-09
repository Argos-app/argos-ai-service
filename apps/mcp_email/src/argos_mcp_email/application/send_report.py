"""Caso de uso para enviar um relatório preparado."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from argos_mcp_email.domain import DispatchState, ReportNotFoundError
from argos_mcp_email.domain.rules import (
    ensure_dispatch_transition,
    ensure_idempotency_consistency,
    ensure_report_usable,
)
from argos_mcp_email.ports.dispatch_repository import DispatchRepository
from argos_mcp_email.ports.mail_provider import MailProvider
from argos_mcp_email.ports.report_repository import ReportRepository
from packages.contracts import DispatchStatus, SendReportRequest, SendReportResult


class SendReport:
    """Coordena o envio sem repetir operações já registradas."""

    def __init__(
        self,
        *,
        report_repository: ReportRepository,
        dispatch_repository: DispatchRepository,
        mail_provider: MailProvider,
        sender: str,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reports = report_repository
        self._dispatches = dispatch_repository
        self._provider = mail_provider
        self._sender = sender
        self._id_factory = id_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(self, request: SendReportRequest) -> SendReportResult:
        existing = await self._dispatches.find_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            ensure_idempotency_consistency(
                existing,
                report_id=request.report_id,
                requested_by=request.requested_by,
            )
            return self._to_result(existing)

        report = await self._reports.get(request.report_id)
        if report is None:
            raise ReportNotFoundError("relatório preparado não encontrado")
        ensure_report_usable(report, requested_by=request.requested_by, now=self._clock())

        operation = await self._dispatches.create_pending(
            operation_id=self._id_factory(),
            report_id=report.report_id,
            requested_by=request.requested_by,
            idempotency_key=request.idempotency_key,
        )
        ensure_idempotency_consistency(
            operation,
            report_id=request.report_id,
            requested_by=request.requested_by,
        )
        if operation.state != DispatchState.PENDING:
            return self._to_result(operation)
        if not await self._dispatches.claim_for_send(operation.operation_id):
            current = await self._dispatches.find_by_idempotency_key(request.idempotency_key)
            return self._to_result(current or operation)

        recipients = tuple(_recipient_ref(recipient) for recipient in report.recipients)
        try:
            provider_result = await self._provider.send(
                sender=self._sender,
                recipients=recipients,
                subject=report.subject,
                body=report.body,
            )
            target_state = (
                DispatchState.UNKNOWN
                if provider_result.delivery_unknown
                else DispatchState.PARTIAL
                if provider_result.refused_recipient_count > 0
                else DispatchState.ACCEPTED
                if provider_result.accepted
                else DispatchState.FAILED
            )
            error_code = provider_result.error_code or (
                "partial_recipient_refusal" if provider_result.refused_recipient_count > 0 else None
            )
            provider_message_id = provider_result.provider_message_id
        except Exception:
            target_state = DispatchState.UNKNOWN
            error_code = "provider_result_unknown"
            provider_message_id = None

        ensure_dispatch_transition(operation.state, target_state)
        updated = await self._dispatches.update_state(
            operation.operation_id,
            target_state,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )
        return self._to_result(updated)

    @staticmethod
    def _to_result(operation) -> SendReportResult:
        return SendReportResult(
            operation_id=operation.operation_id,
            report_id=operation.report_id,
            status=DispatchStatus(operation.state.value),
            provider_message_id=operation.provider_message_id,
            error_code=operation.error_code,
            created_at=operation.created_at,
            updated_at=operation.updated_at,
        )


def _recipient_ref(recipient):
    from packages.contracts import RecipientRef

    return RecipientRef(
        recipient_id=recipient.recipient_id,
        name=recipient.name,
        email=recipient.email,
    )
