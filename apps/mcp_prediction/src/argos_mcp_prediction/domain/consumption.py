"""Modelos internos imutáveis; sem dependência de transporte, SDK ou banco."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class ConsumptionUnit(StrEnum):
    ML = "ml"
    MG = "mg"
    G = "g"
    IU = "UI"
    DOSE = "dose"


class ConsumptionState(StrEnum):
    ESTIMATED = "estimated"
    INSUFFICIENT_DATA = "insufficient_data"
    INCONSISTENT_DATA = "inconsistent_data"


class CoverageState(StrEnum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class ConsumptionGroup:
    property_id: UUID
    medicine_id: UUID
    unit: ConsumptionUnit
    medicine_name: str
    active_ingredient: str


@dataclass(frozen=True, slots=True)
class MonthlyConsumptionRecord:
    group: ConsumptionGroup
    month: int
    quantity: Decimal
    application_events: int
    head_administrations: int


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    property_id: UUID
    state: CoverageState
    start: date | None = None
    end: date | None = None
    source: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConsumptionEstimate:
    group: ConsumptionGroup
    state: ConsumptionState
    quantity: Decimal | None = None
    application_events: Decimal | None = None
    head_administrations: Decimal | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PredictionWindows:
    history_start: date
    history_end: date
    forecast_start: date
    forecast_end: date


@dataclass(frozen=True, slots=True)
class MonthlyConsumptionSummary:
    month: int
    calendar_days: int
    quantity: Decimal
    application_events: int
    head_administrations: int


@dataclass(frozen=True, slots=True)
class ConsumptionCalculation:
    estimate: ConsumptionEstimate
    monthly_history: tuple[MonthlyConsumptionSummary, ...]
    historical_quantity: Decimal
    historical_application_events: int
    historical_head_administrations: int
