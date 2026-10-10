"""Regras puras de continuidade do consumo por mês do ano.

Janelas são inclusivas. A estimativa usa Decimal com precisão local de 50 dígitos,
sem arredondar contagens fracionárias ou converter medicamentos/unidades.
"""

from datetime import date, timedelta
from decimal import Context, Decimal, localcontext
from uuid import UUID

from .consumption import (
    ConsumptionCalculation,
    ConsumptionEstimate,
    ConsumptionGroup,
    ConsumptionState,
    ConsumptionUnit,
    CoverageRecord,
    CoverageState,
    MonthlyConsumptionRecord,
    MonthlyConsumptionSummary,
    PredictionWindows,
)
from .errors import InvalidConsumptionDataError


def _annual_start(as_of: date) -> date:
    # Em 29/02, o aniversário anterior é 28/02. A janela começa no dia seguinte.
    try:
        anniversary = as_of.replace(year=as_of.year - 1)
    except ValueError:
        anniversary = date(as_of.year - 1, 2, 28)
    return anniversary + timedelta(days=1)


def build_prediction_windows(
    as_of: date,
    horizon_days: int = 28,
    *,
    today: date | None = None,
) -> PredictionWindows:
    current_date = today if today is not None else date.today()
    if type(as_of) is not date or as_of.year < 2 or as_of >= current_date:
        raise ValueError("as_of deve ser um dia encerrado com um ano histórico representável")
    if type(horizon_days) is not int or not 1 <= horizon_days <= 28:
        raise ValueError("horizonte deve estar entre 1 e 28 dias")
    return PredictionWindows(
        _annual_start(as_of),
        as_of,
        as_of + timedelta(days=1),
        as_of + timedelta(days=horizon_days),
    )


def _validate_windows(windows: PredictionWindows) -> None:
    if not isinstance(windows, PredictionWindows) or any(
        type(value) is not date
        for value in (
            windows.history_start,
            windows.history_end,
            windows.forecast_start,
            windows.forecast_end,
        )
    ):
        raise ValueError("janelas de predição inválidas")
    if windows.history_end.year < 2:
        raise ValueError("janela histórica inválida")
    horizon = (windows.forecast_end - windows.history_end).days
    if (
        windows.history_start != _annual_start(windows.history_end)
        or windows.forecast_start != windows.history_end + timedelta(days=1)
        or not 1 <= horizon <= 28
    ):
        raise ValueError("janelas devem corresponder a um ano histórico e ao horizonte futuro")


def _calendar_days(start: date, end: date) -> dict[int, int]:
    counts = dict.fromkeys(range(1, 13), 0)
    for offset in range((end - start).days + 1):
        counts[(start + timedelta(days=offset)).month] += 1
    return counts


def coverage_failure_reason(
    coverage: CoverageRecord | None,
    *,
    property_id: UUID,
    windows: PredictionWindows,
) -> str | None:
    """Cobertura só é utilizável com evidência explícita cobrindo o ano completo."""
    _validate_windows(windows)
    if coverage is None:
        return "cobertura histórica não comprovada"
    if not isinstance(coverage, CoverageRecord) or coverage.property_id != property_id:
        raise InvalidConsumptionDataError("evidência de cobertura externa à propriedade")
    if not isinstance(coverage.state, CoverageState):
        raise InvalidConsumptionDataError("estado de cobertura inválido")
    if coverage.state != CoverageState.CONFIRMED:
        return coverage.reason or "cobertura histórica desconhecida ou incompleta"
    if (
        not isinstance(coverage.source, str)
        or not coverage.source.strip()
        or coverage.start is None
        or coverage.end is None
    ):
        return "cobertura confirmada sem fonte ou janela de evidência"
    if (
        type(coverage.start) is not date
        or type(coverage.end) is not date
        or coverage.start > coverage.end
    ):
        raise InvalidConsumptionDataError("janela de cobertura inválida")
    if coverage.start > windows.history_start or coverage.end < windows.history_end:
        return "evidência não cobre todo o ano histórico"
    return None


def calculate_consumption(
    group: ConsumptionGroup,
    records: tuple[MonthlyConsumptionRecord, ...],
    *,
    coverage: CoverageRecord | None,
    windows: PredictionWindows,
) -> ConsumptionCalculation:
    """Calcula um grupo isolado pela distribuição mensal do histórico disponível.

    records deve vir da seleção exata da janela no repositório autorizado. Como a
    completude do histórico não é confirmada neste MVP, meses sem eventos são
    tratados como zero observado; a resposta precisa expor essa limitação.
    """
    _validate_windows(windows)
    if (
        not isinstance(group, ConsumptionGroup)
        or not isinstance(group.property_id, UUID)
        or not isinstance(group.medicine_id, UUID)
        or not isinstance(group.unit, ConsumptionUnit)
    ):
        raise InvalidConsumptionDataError("grupo de consumo inválido")
    # A evidência continua sendo validada para evitar associação a outra
    # propriedade, mas sua ausência ou incompletude não bloqueia o cálculo.
    if coverage is not None:
        coverage_failure_reason(coverage, property_id=group.property_id, windows=windows)
    by_month = {}
    for record in records:
        if (
            not isinstance(record, MonthlyConsumptionRecord)
            or record.group != group
            or type(record.month) is not int
            or not 1 <= record.month <= 12
            or record.month in by_month
            or not isinstance(record.quantity, Decimal)
            or not record.quantity.is_finite()
            or record.quantity <= 0
            or type(record.application_events) is not int
            or record.application_events < 1
            or type(record.head_administrations) is not int
            or record.head_administrations < record.application_events
        ):
            raise InvalidConsumptionDataError("histórico mensal inconsistente")
        by_month[record.month] = record
    reason = None if records else "medicamento sem histórico de aplicações na janela"
    history_days = _calendar_days(windows.history_start, windows.history_end)
    future_days = _calendar_days(windows.forecast_start, windows.forecast_end)
    with localcontext(Context(prec=50)):
        historical_quantity = sum((record.quantity for record in records), Decimal(0))
        historical_events = sum(record.application_events for record in records)
        historical_heads = sum(record.head_administrations for record in records)
        summaries = tuple(
            MonthlyConsumptionSummary(
                month,
                history_days[month],
                by_month[month].quantity if month in by_month else Decimal(0),
                by_month[month].application_events if month in by_month else 0,
                by_month[month].head_administrations if month in by_month else 0,
            )
            for month in range(1, 13)
        )
        if reason is not None:
            estimate = ConsumptionEstimate(
                group,
                ConsumptionState.INSUFFICIENT_DATA,
                reason=reason,
            )
        else:
            quantity, events, heads = Decimal(0), Decimal(0), Decimal(0)
            for summary in summaries:
                days = Decimal(summary.calendar_days)
                future = Decimal(future_days[summary.month])
                quantity += summary.quantity * future / days
                events += Decimal(summary.application_events) * future / days
                heads += Decimal(summary.head_administrations) * future / days
            estimate = ConsumptionEstimate(
                group, ConsumptionState.ESTIMATED, quantity, events, heads
            )
        return ConsumptionCalculation(
            estimate,
            summaries if records else (),
            historical_quantity,
            historical_events,
            historical_heads,
        )
