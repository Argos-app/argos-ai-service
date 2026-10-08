"""Contratos das consultas agregadas de efetivo e animais de exceção."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from argos_mcp_data.domain.cattle import _Model

FarmGroup = Literal["property", "herd", "lot_category", "exception_lot"]
ExceptionGroup = Literal["property", "herd", "lot", "status", "category", "reason"]
AnimalStatus = Literal["ATIVO", "VENDIDO", "ABATIDO", "MORTO", "RECUPERADO"]
AnimalCategory = Literal["BEZERRO", "GARROTE", "BOI", "TOURO"]


class _AggregateRequest(_Model):
    property_ids: tuple[UUID, ...] | None = Field(default=None, min_length=1, max_length=100)
    herd_id: UUID | None = None
    lot_id: UUID | None = None
    page_size: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


class FarmSummaryRequest(_AggregateRequest):
    lot_category: Literal["ENGORDA", "CRIA", "RECRIA"] | None = None
    active: bool | None = True
    is_exception_lot: bool | None = None
    group_by: FarmGroup = "property"


class ExceptionSummaryRequest(_AggregateRequest):
    statuses: tuple[AnimalStatus, ...] | None = Field(default=None, min_length=1, max_length=5)
    category: AnimalCategory | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=120)
    date_from: date | None = None
    date_to: date | None = None
    group_by: ExceptionGroup = "status"

    @model_validator(mode="after")
    def validate_filters(self):
        if self.date_from and self.date_to:
            if self.date_from > self.date_to:
                raise ValueError("date_from deve ser anterior ou igual a date_to")
            if (self.date_to - self.date_from).days > 366:
                raise ValueError("o intervalo máximo de consulta é de 366 dias")
        if self.statuses is not None and len(set(self.statuses)) != len(self.statuses):
            raise ValueError("statuses não pode conter duplicatas")
        if self.reason is not None and len(self.reason) > 120:
            raise ValueError("reason excede o limite de 120 caracteres")
        return self


class AggregatePage(_Model):
    items: tuple[dict[str, object], ...]
    page_size: int = Field(ge=1, le=100)
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0, le=10_000)
