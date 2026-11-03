"""Caso de uso para consultar o estado de uma operação de envio."""

from argos_mcp_email.domain import ReportNotFoundError, ReportOwnershipError
from argos_mcp_email.ports.dispatch_repository import DispatchRepository
from packages.contracts import ReportStatusRequest, ReportStatusResult


class GetReportStatus:
    def __init__(self, *, dispatch_repository: DispatchRepository) -> None:
        self._dispatches = dispatch_repository

    async def execute(self, request: ReportStatusRequest) -> ReportStatusResult:
        operation = await self._dispatches.get(request.operation_id)
        if operation is None:
            raise ReportNotFoundError("operação de envio não encontrada")
        if operation.requested_by != request.requested_by:
            raise ReportOwnershipError("operação pertence a outro solicitante")
        return ReportStatusResult(
            operation_id=operation.operation_id,
            report_id=operation.report_id,
            status=operation.state.value,
            provider_message_id=operation.provider_message_id,
            error_code=operation.error_code,
            created_at=operation.created_at,
            updated_at=operation.updated_at,
        )
