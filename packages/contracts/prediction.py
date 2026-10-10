"""Contratos da estimativa de consumo; identidade vem da delegação protegida."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class _PredictionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ConsumptionUnit(StrEnum):
    ML = "ml"
    MG = "mg"
    G = "g"
    IU = "UI"
    DOSE = "dose"


class PredictionStatus(StrEnum):
    ESTIMATED = "estimated"
    INSUFFICIENT_DATA = "insufficient_data"
    INCONSISTENT_DATA = "inconsistent_data"


class CoverageStatus(StrEnum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    INCOMPLETE = "incomplete"


class DataOrigin(StrEnum):
    RECORDED = "recorded"
    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


class PredictionMode(StrEnum):
    HISTORICAL = "historical"
    SIMULATION = "simulation"


IdFilter = Annotated[tuple[UUID, ...], Field(min_length=1, max_length=100)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
NonNegativeCount = Annotated[StrictInt, Field(ge=0)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


def _date_input(value):
    if isinstance(value, datetime) or not isinstance(value, (str, date)):
        raise ValueError("informe uma data de calendário, sem horário")
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("data deve usar YYYY-MM-DD") from exc
        if parsed.isoformat() != value:
            raise ValueError("data deve usar YYYY-MM-DD")
    return value


def _unique_ids(value):
    if value is not None and len(set(value)) != len(value):
        raise ValueError("IDs duplicados não são permitidos")
    return value


class EstimateMedicineConsumptionRequest(_PredictionContract):
    """Filtros apenas reduzem o escopo validado pelo caso de uso; não concedem acesso."""

    property_ids: IdFilter | None = None
    medicine_ids: IdFilter | None = None
    as_of: date = Field(description="Último dia encerrado do histórico, no calendário do serviço.")
    horizon_days: StrictInt = Field(default=28, ge=1, le=28)

    @field_validator("property_ids", "medicine_ids")
    @classmethod
    def validate_ids(cls, value):
        return _unique_ids(value)

    @field_validator("as_of", mode="before")
    @classmethod
    def validate_date_input(cls, value):
        return _date_input(value)

    @field_validator("as_of")
    @classmethod
    def validate_closed_date(cls, value):
        if value >= date.today():
            raise ValueError("as_of deve ser um dia encerrado, anterior à data atual do serviço")
        return value


class PredictionWindow(_PredictionContract):
    """Limites inclusivos de uma janela de datas."""

    start: date
    end: date

    @field_validator("start", "end", mode="before")
    @classmethod
    def validate_date_input(cls, value):
        return _date_input(value)

    @model_validator(mode="after")
    def validate_order(self):
        if self.start > self.end:
            raise ValueError("janela de datas invertida")
        return self


class PropertyCoverage(_PredictionContract):
    """Evidência definida pelo serviço, nunca declarada pelo agente solicitante."""

    property_id: UUID
    status: CoverageStatus
    window: PredictionWindow | None = None
    source: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.status == CoverageStatus.CONFIRMED and (self.window is None or self.source is None):
            raise ValueError("cobertura confirmada exige janela e fonte de evidência")
        if self.status != CoverageStatus.CONFIRMED and self.reason is None:
            raise ValueError("cobertura desconhecida ou incompleta exige motivo")
        return self


class MonthlyConsumption(_PredictionContract):
    """Resumo do mês do ano dentro da janela; cabeças-administrações não são animais únicos."""

    month: StrictInt = Field(ge=1, le=12)
    calendar_days: StrictInt = Field(ge=1, le=31)
    quantity: NonNegativeDecimal
    application_events: NonNegativeCount
    head_administrations: NonNegativeCount

    @model_validator(mode="after")
    def validate_calendar_days(self):
        maximum = 29 if self.month == 2 else 30 if self.month in (4, 6, 9, 11) else 31
        if self.calendar_days > maximum:
            raise ValueError("quantidade de dias incompatível com o mês do ano")
        return self


class MedicineConsumptionEstimate(_PredictionContract):
    property_id: UUID
    medicine_id: UUID
    unit: ConsumptionUnit
    historical_quantity: NonNegativeDecimal
    historical_application_events: NonNegativeCount
    historical_head_administrations: NonNegativeCount
    monthly_history: tuple[MonthlyConsumption, ...] = Field(default=(), max_length=12)
    estimated_quantity: NonNegativeDecimal | None = None
    estimated_application_events: NonNegativeDecimal | None = None
    estimated_head_administrations: NonNegativeDecimal | None = None

    @model_validator(mode="after")
    def validate_values(self):
        months = [item.month for item in self.monthly_history]
        if len(set(months)) != len(months):
            raise ValueError("mês do ano duplicado no resumo")
        estimates = (
            self.estimated_quantity,
            self.estimated_application_events,
            self.estimated_head_administrations,
        )
        if any(value is None for value in estimates) and not all(
            value is None for value in estimates
        ):
            raise ValueError("estimativas devem ser todas preenchidas ou todas nulas")
        if self.monthly_history and (
            sum(item.quantity for item in self.monthly_history) != self.historical_quantity
            or sum(item.application_events for item in self.monthly_history)
            != self.historical_application_events
            or sum(item.head_administrations for item in self.monthly_history)
            != self.historical_head_administrations
        ):
            raise ValueError("totais históricos divergem do resumo mensal")
        return self


class EstimateMedicineConsumptionResponse(_PredictionContract):
    status: PredictionStatus
    as_of: date
    horizon_days: StrictInt = Field(ge=1, le=28)
    history_window: PredictionWindow
    forecast_window: PredictionWindow
    property_ids: IdFilter
    coverage: tuple[PropertyCoverage, ...] = Field(min_length=1, max_length=100)
    items: tuple[MedicineConsumptionEstimate, ...] = Field(default=(), max_length=1000)
    method: Literal["monthly_daily_average"] = "monthly_daily_average"
    mode: PredictionMode
    data_origin: DataOrigin
    reason: Reason | None = None
    limitations: tuple[Reason, ...] = Field(min_length=1, max_length=20)

    @field_validator("property_ids")
    @classmethod
    def validate_ids(cls, value):
        return _unique_ids(value)

    @field_validator("as_of", mode="before")
    @classmethod
    def validate_date_input(cls, value):
        return _date_input(value)

    @model_validator(mode="after")
    def validate_response(self):
        if self.history_window.end != self.as_of:
            raise ValueError("histórico deve terminar em as_of")
        history_days = (self.history_window.end - self.history_window.start).days + 1
        if history_days not in (365, 366):
            raise ValueError("janela histórica deve ter 365 ou 366 dias")
        if self.forecast_window.start != self.as_of + timedelta(
            days=1
        ) or self.forecast_window.end != self.as_of + timedelta(days=self.horizon_days):
            raise ValueError("janela futura deve corresponder ao horizonte solicitado")
        selected = set(self.property_ids)
        covered = [item.property_id for item in self.coverage]
        if len(set(covered)) != len(covered) or set(covered) != selected:
            raise ValueError("cobertura deve descrever exatamente as propriedades retornadas")
        keys = [(item.property_id, item.medicine_id, item.unit) for item in self.items]
        if len(set(keys)) != len(keys) or any(
            item.property_id not in selected for item in self.items
        ):
            raise ValueError("grupo duplicado ou propriedade externa ao retorno")
        if (self.data_origin == DataOrigin.SYNTHETIC) != (self.mode == PredictionMode.SIMULATION):
            raise ValueError("origem sintética exige modo simulation e vice-versa")
        if self.status == PredictionStatus.ESTIMATED:
            if not self.items or self.data_origin == DataOrigin.UNKNOWN:
                raise ValueError("estimativa exige histórico e origem conhecida")
            if any(
                item.estimated_quantity is None or not item.monthly_history for item in self.items
            ):
                raise ValueError("estimativa exige valores e resumo histórico por grupo")
        elif self.reason is None or any(item.estimated_quantity is not None for item in self.items):
            raise ValueError(
                "dados insuficientes ou inconsistentes exigem motivo e estimativas nulas"
            )
        return self
