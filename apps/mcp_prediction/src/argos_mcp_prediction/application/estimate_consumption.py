"""Caso de uso autorizado para estimar consumo de medicamentos."""

from collections import defaultdict
from uuid import UUID

from argos_mcp_prediction.domain import (
    ConsumptionGroup,
    ConsumptionState,
    ConsumptionUnit,
    CoverageRecord,
    CoverageState,
    MonthlyConsumptionRecord,
    PredictionWindows,
)
from argos_mcp_prediction.domain.errors import InvalidConsumptionDataError
from argos_mcp_prediction.domain.rules import (
    build_prediction_windows,
    calculate_consumption,
    coverage_failure_reason,
)
from argos_mcp_prediction.ports import ConsumptionReadRepository, CoverageReadRepository
from packages.contracts import (
    CoverageStatus,
    DataOrigin,
    EstimateMedicineConsumptionRequest,
    EstimateMedicineConsumptionResponse,
    MedicineConsumptionEstimate,
    MonthlyConsumption,
    PredictionMode,
    PredictionStatus,
    PredictionWindow,
    PropertyCoverage,
)
from packages.security.access import AccessContext, AuthorizedScope
from packages.security.access_policy import AccessPolicy
from packages.security.errors import AccessDeniedError

_OPERATION = "prediction.read"
_LIMITATIONS = (
    "A estimativa repete o padrão mensal observado em um único ano e não prevê mudanças de manejo.",
    "Sem cobertura histórica confirmada, meses sem registros não são tratados como zero.",
    "A estimativa não é apresentada quando a cobertura não está confirmada.",
    "Cabeças-administrações representam aplicações acumuladas, não animais únicos tratados.",
    "Medicamentos e unidades permanecem separados, sem conversão ou recomendação clínica.",
)


class EstimateMedicineConsumption:
    """Coordena autorização, leituras e regras, mantendo autoridade fora do payload."""

    def __init__(
        self,
        *,
        access_policy: AccessPolicy,
        consumption_repository: ConsumptionReadRepository,
        coverage_repository: CoverageReadRepository,
        data_origin: DataOrigin = DataOrigin.UNKNOWN,
    ) -> None:
        if not isinstance(data_origin, DataOrigin):
            raise ValueError("origem dos dados deve ser configurada pelo serviço")
        self._access_policy = access_policy
        self._consumption_repository = consumption_repository
        self._coverage_repository = coverage_repository
        self._data_origin = data_origin

    async def execute(
        self,
        context: AccessContext,
        request: EstimateMedicineConsumptionRequest,
    ) -> EstimateMedicineConsumptionResponse:
        """Autoriza o escopo completo antes de acessar histórico ou cobertura."""

        if not isinstance(context, AccessContext) or context.agent_id != "analista":
            raise AccessDeniedError("ferramenta disponível somente ao agente Analista")
        if not isinstance(request, EstimateMedicineConsumptionRequest):
            raise ValueError("payload de estimativa inválido")

        scope = await self._access_policy.authorize(
            context,
            operation=_OPERATION,
            selected_property_ids=request.property_ids,
        )
        windows = build_prediction_windows(request.as_of, request.horizon_days)

        # Revalidar os tipos aqui protege o caso de uso quando uma implementação
        # falsa ou outro adapter não respeita o contrato Protocol.
        coverage_records = await self._coverage_repository.get_coverage(
            scope, start=windows.history_start, end=windows.history_end
        )
        coverage_by_property = self._index_coverage(scope, coverage_records)
        coverage_response = self._coverage_response(scope, coverage_by_property, windows)
        reasons = [
            item.reason or "cobertura histórica não confirmada"
            for item in coverage_response
            if item.status != CoverageStatus.CONFIRMED
        ]

        try:
            records = await self._consumption_repository.aggregate_monthly(
                scope,
                start=windows.history_start,
                end=windows.history_end,
                medicine_names=request.medicine_names,
            )
        except InvalidConsumptionDataError:
            return self._inconsistent_response(scope, request, windows, coverage_response)

        groups: dict[ConsumptionGroup, list[MonthlyConsumptionRecord]] = defaultdict(list)
        if not isinstance(records, tuple):
            raise InvalidConsumptionDataError("repositório retornou uma resposta inválida")
        for record in records:
            if (
                not isinstance(record, MonthlyConsumptionRecord)
                or not isinstance(record.group, ConsumptionGroup)
                or not isinstance(record.group.unit, ConsumptionUnit)
            ):
                raise InvalidConsumptionDataError("repositório retornou um registro inválido")
            if record.group.property_id not in scope.property_ids:
                raise InvalidConsumptionDataError(
                    "repositório retornou uma propriedade externa ao escopo"
                )
            if request.medicine_names is not None and not self._matches_any_name(
                record.group, request.medicine_names
            ):
                raise InvalidConsumptionDataError("repositório ignorou o filtro por nome")
            groups[record.group].append(record)

        calculations = []
        if request.medicine_names is not None:
            matched_names = {
                (group.property_id, name.casefold())
                for group in groups
                for name in request.medicine_names
                if self._matches_name(group, name)
            }
            missing_names = [
                (property_id, name)
                for property_id in scope.property_ids
                for name in request.medicine_names
                if (property_id, name.casefold()) not in matched_names
            ]
            if missing_names:
                reasons.append(
                    "Não foi encontrado histórico de aplicação para todos os nomes solicitados "
                    "em cada propriedade selecionada; não é possível afirmar consumo zero."
                )
        for group, monthly_records in sorted(
            groups.items(),
            key=lambda item: (
                str(item[0].property_id),
                str(item[0].medicine_id),
                item[0].unit.value,
            ),
        ):
            try:
                calculation = calculate_consumption(
                    group,
                    tuple(monthly_records),
                    coverage=coverage_by_property[group.property_id],
                    windows=windows,
                )
            except InvalidConsumptionDataError:
                return self._inconsistent_response(scope, request, windows, coverage_response)
            calculations.append(calculation)
            if calculation.estimate.state != ConsumptionState.ESTIMATED:
                reasons.append(calculation.estimate.reason or "histórico insuficiente")

        if self._data_origin == DataOrigin.UNKNOWN:
            reasons.append("a origem do histórico ainda não está configurada pelo serviço")
        if not calculations:
            reasons.append("não há aplicações do medicamento no histórico selecionado")

        status = PredictionStatus.ESTIMATED if not reasons else PredictionStatus.INSUFFICIENT_DATA
        response_items = []
        for calculation in calculations:
            estimate = calculation.estimate
            response_items.append(
                MedicineConsumptionEstimate(
                    property_id=estimate.group.property_id,
                    medicine_id=estimate.group.medicine_id,
                    medicine_name=estimate.group.medicine_name,
                    active_ingredient=estimate.group.active_ingredient,
                    unit=estimate.group.unit.value,
                    historical_quantity=calculation.historical_quantity,
                    historical_application_events=calculation.historical_application_events,
                    historical_head_administrations=calculation.historical_head_administrations,
                    monthly_history=tuple(
                        MonthlyConsumption(
                            month=item.month,
                            calendar_days=item.calendar_days,
                            quantity=item.quantity,
                            application_events=item.application_events,
                            head_administrations=item.head_administrations,
                        )
                        for item in calculation.monthly_history
                    ),
                    estimated_quantity=estimate.quantity
                    if status == PredictionStatus.ESTIMATED
                    else None,
                    estimated_application_events=(
                        estimate.application_events
                        if status == PredictionStatus.ESTIMATED
                        else None
                    ),
                    estimated_head_administrations=(
                        estimate.head_administrations
                        if status == PredictionStatus.ESTIMATED
                        else None
                    ),
                )
            )

        return self._response(
            scope=scope,
            request=request,
            windows=windows,
            coverage=coverage_response,
            status=status,
            reason=None if status == PredictionStatus.ESTIMATED else self._reason(reasons),
            items=tuple(response_items),
        )

    @staticmethod
    def _matches_name(group: ConsumptionGroup, name: str) -> bool:
        query = name.strip().casefold()
        return (
            query in group.medicine_name.casefold() or query in group.active_ingredient.casefold()
        )

    @classmethod
    def _matches_any_name(cls, group: ConsumptionGroup, names: tuple[str, ...]) -> bool:
        return any(cls._matches_name(group, name) for name in names)

    @staticmethod
    def _index_coverage(
        scope: AuthorizedScope,
        records: tuple[CoverageRecord, ...],
    ) -> dict[UUID, CoverageRecord]:
        if not isinstance(records, tuple):
            raise InvalidConsumptionDataError("resposta de cobertura inválida")
        indexed: dict[UUID, CoverageRecord] = {}
        for record in records:
            if (
                not isinstance(record, CoverageRecord)
                or record.property_id not in scope.property_ids
            ):
                raise InvalidConsumptionDataError("cobertura externa ao escopo autorizado")
            if record.property_id in indexed:
                raise InvalidConsumptionDataError("cobertura duplicada para a propriedade")
            indexed[record.property_id] = record
        if set(indexed) != set(scope.property_ids):
            raise InvalidConsumptionDataError("cobertura ausente para propriedade autorizada")
        return indexed

    @staticmethod
    def _coverage_response(
        scope: AuthorizedScope,
        coverage_by_property: dict[UUID, CoverageRecord],
        windows: PredictionWindows,
    ) -> tuple[PropertyCoverage, ...]:
        response = []
        for property_id in scope.property_ids:
            coverage = coverage_by_property[property_id]
            reason = coverage_failure_reason(coverage, property_id=property_id, windows=windows)
            if reason is not None:
                status = (
                    CoverageStatus.UNKNOWN
                    if coverage.state == CoverageState.UNKNOWN
                    else CoverageStatus.INCOMPLETE
                )
                response.append(
                    PropertyCoverage(
                        property_id=property_id,
                        status=status,
                        reason=EstimateMedicineConsumption._reason([reason]),
                    )
                )
                continue
            response.append(
                PropertyCoverage(
                    property_id=property_id,
                    status=CoverageStatus.CONFIRMED,
                    window=PredictionWindow(start=coverage.start, end=coverage.end),
                    source=coverage.source,
                )
            )
        return tuple(response)

    @staticmethod
    def _reason(reasons: list[str]) -> str:
        return "; ".join(dict.fromkeys(reason.strip() for reason in reasons if reason.strip()))[
            :500
        ]

    def _inconsistent_response(
        self,
        scope: AuthorizedScope,
        request: EstimateMedicineConsumptionRequest,
        windows: PredictionWindows,
        coverage: tuple[PropertyCoverage, ...],
    ) -> EstimateMedicineConsumptionResponse:
        return self._response(
            scope=scope,
            request=request,
            windows=windows,
            coverage=coverage,
            status=PredictionStatus.INCONSISTENT_DATA,
            reason=(
                "Os registros de aplicação apresentam divergências e não permitem "
                "estimar o consumo."
            ),
            items=(),
        )

    def _response(
        self,
        *,
        scope: AuthorizedScope,
        request: EstimateMedicineConsumptionRequest,
        windows: PredictionWindows,
        coverage: tuple[PropertyCoverage, ...],
        status: PredictionStatus,
        reason: str | None,
        items: tuple[MedicineConsumptionEstimate, ...],
    ) -> EstimateMedicineConsumptionResponse:
        return EstimateMedicineConsumptionResponse(
            status=status,
            as_of=request.as_of,
            horizon_days=request.horizon_days,
            history_window=PredictionWindow(start=windows.history_start, end=windows.history_end),
            forecast_window=PredictionWindow(
                start=windows.forecast_start, end=windows.forecast_end
            ),
            property_ids=scope.property_ids,
            coverage=coverage,
            items=items,
            mode=(
                PredictionMode.SIMULATION
                if self._data_origin == DataOrigin.SYNTHETIC
                else PredictionMode.HISTORICAL
            ),
            data_origin=self._data_origin,
            reason=reason,
            limitations=_LIMITATIONS,
        )
