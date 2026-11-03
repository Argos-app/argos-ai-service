"""Filtros e projeções tipadas das consultas SQL detalhadas de pecuária."""

from datetime import date, time
from decimal import Decimal
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ScopedPageRequest(_Model):
    property_ids: tuple[UUID, ...] | None = Field(default=None, min_length=1, max_length=100)
    page_size: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def property_ids_are_unique(self):
        if self.property_ids is not None and len(set(self.property_ids)) != len(self.property_ids):
            raise ValueError("property_ids não pode conter duplicatas")
        return self


class DateRangeRequest(ScopedPageRequest):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from deve ser anterior ou igual a date_to")
        if self.date_from and self.date_to and (self.date_to - self.date_from).days > 366:
            raise ValueError("o intervalo máximo de consulta é de 366 dias")
        return self


class ListPropertiesRequest(ScopedPageRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None


class ListHerdsRequest(ScopedPageRequest):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    breed: str | None = Field(default=None, min_length=1, max_length=40)
    active: bool | None = None


class ListLotsRequest(DateRangeRequest):
    lot_id: UUID | None = None
    herd_id: UUID | None = None
    lot_code: str | None = Field(default=None, min_length=1, max_length=20)
    category: Literal["ENGORDA", "CRIA", "RECRIA"] | None = None
    is_exception_lot: bool | None = None
    active: bool | None = None
    head_count_min: int | None = Field(default=None, ge=0)
    head_count_max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_head_count_range(self):
        if (
            self.head_count_min is not None
            and self.head_count_max is not None
            and self.head_count_min > self.head_count_max
        ):
            raise ValueError("head_count_min deve ser menor ou igual a head_count_max")
        return self


class GetLotReportSourceRequest(_Model):
    """Identidade do lote e da propriedade autorizada para montar o relatório."""

    lot_id: UUID
    property_id: UUID


class ListExceptionAnimalsRequest(DateRangeRequest):
    lot_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)
    category: Literal["BEZERRO", "GARROTE", "BOI", "TOURO"] | None = None
    statuses: tuple[Literal["ATIVO", "VENDIDO", "ABATIDO", "MORTO", "RECUPERADO"], ...] | None = (
        Field(default=None, min_length=1, max_length=5)
    )

    @model_validator(mode="after")
    def statuses_are_unique(self):
        if self.statuses is not None and len(set(self.statuses)) != len(self.statuses):
            raise ValueError("statuses não pode conter duplicatas")
        return self


class ListApplicationsRequest(DateRangeRequest):
    lot_id: UUID | None = None
    medicine_id: UUID | None = None
    medicine_name: str | None = Field(default=None, min_length=1, max_length=80)
    active: bool | None = None
    application_kind: Literal["COLETIVA", "INDIVIDUAL"] | None = None


class ListApplicationAnimalsRequest(DateRangeRequest):
    application_id: UUID | None = None
    lot_id: UUID | None = None
    medicine_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)


class ListOccurrencesRequest(DateRangeRequest):
    animal_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)
    occurrence_type: Literal["DOENCA", "MORTE", "REACAO_ADVERSA", "FERIMENTO"] | None = None
    resolved: bool | None = None


class ListAnimalLotHistoryRequest(DateRangeRequest):
    animal_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)
    lot_id: UUID | None = None


class Page[T](_Model):
    items: tuple[T, ...]
    page_size: int = Field(ge=1, le=100)
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0, le=10_000)


class PropertyRecord(_Model):
    property_id: UUID
    property_name: str
    city: str | None
    state: str | None
    active: bool


class HerdRecord(_Model):
    herd_id: UUID
    property_id: UUID
    property_name: str
    herd_name: str
    breed: str
    head_count: int
    active: bool


class LotRecord(_Model):
    lot_id: UUID
    property_id: UUID
    property_name: str
    herd_id: UUID
    herd_name: str
    herd_breed: str
    lot_code: str
    category: str
    is_exception_lot: bool
    exception_reason: str | None
    head_count: int
    slaughter_release_date: date | None
    fit_for_slaughter: bool
    open_date: date
    close_date: date | None
    active: bool


class ExceptionAnimalRecord(_Model):
    animal_id: UUID
    property_id: UUID
    property_name: str
    current_lot_id: UUID
    current_lot_code: str
    origin_lot_id: UUID
    origin_lot_code: str
    ear_tag: str
    category: str
    birth_date: date | None
    current_weight: Decimal | None
    exception_date: date
    exception_end_date: date | None
    exception_reason: str
    slaughter_release_date: date | None
    fit_for_slaughter: bool
    status: str


class ApplicationRecord(_Model):
    application_id: UUID
    property_id: UUID
    property_name: str
    lot_id: UUID
    lot_code: str
    application_kind: Literal["COLETIVA", "INDIVIDUAL"]
    medicine_id: UUID
    medicine_name: str
    active_ingredient: str
    medicine_batch_id: UUID
    medicine_batch_number: str
    application_date: date
    application_time: time | None
    dose: Decimal
    unit: str
    route: str
    head_count_applied: int
    meat_withdrawal_end: date | None
    goal: str | None
    active: bool


class ApplicationAnimalRecord(_Model):
    application_id: UUID
    property_id: UUID
    application_date: date
    lot_id: UUID
    lot_code: str
    medicine_id: UUID
    medicine_name: str
    animal_id: UUID
    ear_tag: str
    animal_status: str
    dose_given: Decimal
    weight_at_time: Decimal | None
    meat_withdrawal_end: date | None
    released_for_slaughter: bool


class OccurrenceRecord(_Model):
    occurrence_id: UUID
    animal_id: UUID
    ear_tag: str
    animal_status: str
    current_property_id: UUID
    current_property_name: str
    current_lot_id: UUID
    current_lot_code: str
    occurrence_type: str
    occurrence_date: date
    description: str
    diagnosis: str | None
    resolved: bool
    resolved_at: date | None


class AnimalLotHistoryRecord(_Model):
    history_id: UUID
    animal_id: UUID
    ear_tag: str
    animal_status: str
    property_id: UUID
    property_name: str
    lot_id: UUID
    lot_code: str
    entry_date: date
    exit_date: date | None
    reason: str
