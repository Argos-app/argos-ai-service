"""Contratos das consultas agregadas de efetivo e animais de exceção."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from argos_mcp_data.domain.cattle import ScopedPageRequest, _Model

FarmGroup = Literal["property", "herd", "lot", "lot_category", "exception_lot", "active"]
ExceptionGroup = Literal["property", "herd", "lot", "status", "category", "reason"]
AnimalStatus = Literal["ATIVO", "VENDIDO", "ABATIDO", "MORTO", "RECUPERADO"]
AnimalCategory = Literal["BEZERRO", "GARROTE", "BOI", "TOURO"]


class _AggregateRequest(ScopedPageRequest):
    herd_id: UUID | None = None
    lot_id: UUID | None = None


class FarmSummaryRequest(_AggregateRequest):
    """Filtra lotes antes de agrupar; active=None inclui ativos e inativos."""

    lot_category: Literal["ENGORDA", "CRIA", "RECRIA"] | None = None
    active: bool | None = True
    is_exception_lot: bool | None = None
    head_count_min: int | None = Field(default=None, ge=0)
    head_count_max: int | None = Field(default=None, ge=0)
    group_by: FarmGroup = "property"

    @model_validator(mode="after")
    def validate_head_count_range(self):
        if (
            self.head_count_min is not None
            and self.head_count_max is not None
            and self.head_count_min > self.head_count_max
        ):
            raise ValueError("head_count_min deve ser menor ou igual a head_count_max")
        return self


class ExceptionSummaryRequest(_AggregateRequest):
    statuses: tuple[AnimalStatus, ...] | None = Field(default=None, min_length=1, max_length=5)
    category: AnimalCategory | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)
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
