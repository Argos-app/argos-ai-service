"""Regras puras aplicadas pelos casos de uso do MCP de e-mail."""

from datetime import UTC, datetime
from uuid import UUID

from .errors import (
    IdempotencyConflictError,
    InvalidDispatchTransitionError,
    ReportExpiredError,
    ReportOwnershipError,
    UnresolvedRecipientsError,
)
from .models import DispatchRecord, DispatchState, PreparedReportRecord, ReportState


def utc_now() -> datetime:
    """Retorna o relógio usado pelas regras, sempre com timezone UTC."""

    return datetime.now(UTC)


def ensure_report_owner(report: PreparedReportRecord, requested_by: UUID) -> None:
    if report.requested_by != requested_by:
        raise ReportOwnershipError("relatório pertence a outro solicitante")


def ensure_report_usable(
    report: PreparedReportRecord,
    *,
    requested_by: UUID,
    now: datetime | None = None,
) -> None:
    """Confere titularidade, estado, validade e destinatários resolvidos."""

    ensure_report_owner(report, requested_by)
    current_time = now or utc_now()
    if report.state != ReportState.PREPARED or current_time >= report.expires_at:
        raise ReportExpiredError("relatório preparado expirado ou indisponível")
    if not report.recipients:
        raise UnresolvedRecipientsError("relatório não possui destinatários resolvidos")


def ensure_idempotency_consistency(
    existing: DispatchRecord,
    *,
    report_id: UUID,
    requested_by: UUID,
) -> None:
    """Impede reutilizar uma chave para outro relatório ou solicitante."""

    if existing.report_id != report_id or existing.requested_by != requested_by:
        raise IdempotencyConflictError("chave de idempotência usada com dados diferentes")


_ALLOWED_TRANSITIONS: dict[DispatchState, frozenset[DispatchState]] = {
    DispatchState.PENDING: frozenset(
        {
            DispatchState.ACCEPTED,
            DispatchState.PARTIAL,
            DispatchState.FAILED,
            DispatchState.UNKNOWN,
        }
    ),
    DispatchState.ACCEPTED: frozenset(),
    DispatchState.PARTIAL: frozenset(),
    DispatchState.FAILED: frozenset(),
    DispatchState.UNKNOWN: frozenset(),
}


def ensure_dispatch_transition(current: DispatchState, target: DispatchState) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidDispatchTransitionError(
            f"transição de envio inválida: {current.value} -> {target.value}"
        )
