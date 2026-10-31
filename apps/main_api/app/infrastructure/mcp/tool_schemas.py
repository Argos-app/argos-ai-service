"""Schemas públicos das entradas MCP expostas aos agentes do sistema principal."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts import EstimateMedicineConsumptionRequest


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _ScopedPagePayload(_Payload):
    property_ids: tuple[UUID, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description=(
            "IDs de propriedades já informados pelo usuário; omita se não foram informados."
        ),
    )
    page_size: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


class _DateRangePayload(_ScopedPagePayload):
    date_from: date | None = None
    date_to: date | None = None


class ListPropertiesPayload(_ScopedPagePayload):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None


class ListHerdsPayload(_ScopedPagePayload):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    breed: str | None = Field(default=None, min_length=1, max_length=40)
    active: bool | None = None


class ListLotsPayload(_DateRangePayload):
    lot_id: UUID | None = None
    herd_id: UUID | None = None
    lot_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="Código do lote, por exemplo HC-LOTE-001. O nome do campo é lot_code.",
    )
    category: Literal["ENGORDA", "CRIA", "RECRIA"] | None = None
    is_exception_lot: bool | None = None
    active: bool | None = None
    head_count_min: int | None = Field(default=None, ge=0)
    head_count_max: int | None = Field(default=None, ge=0)


class ListExceptionAnimalsPayload(_DateRangePayload):
    lot_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)
    category: Literal["BEZERRO", "GARROTE", "BOI", "TOURO"] | None = None
    statuses: tuple[Literal["ATIVO", "VENDIDO", "ABATIDO", "MORTO", "RECUPERADO"], ...] | None = (
        Field(default=None, min_length=1, max_length=5)
    )


class ListApplicationsPayload(_DateRangePayload):
    lot_id: UUID | None = None
    medicine_id: UUID | None = None
    medicine_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="Filtro textual chamado medicine_name (singular).",
    )
    active: bool | None = None
    application_kind: Literal["COLETIVA", "INDIVIDUAL"] | None = None


class ListApplicationAnimalsPayload(_DateRangePayload):
    application_id: UUID | None = None
    lot_id: UUID | None = None
    medicine_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)


class ListOccurrencesPayload(_DateRangePayload):
    animal_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)
    occurrence_type: Literal["DOENCA", "MORTE", "REACAO_ADVERSA", "FERIMENTO"] | None = None
    resolved: bool | None = None


class ListAnimalLotHistoryPayload(_DateRangePayload):
    animal_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)
    lot_id: UUID | None = None


class ListCattleDocumentsPayload(_ScopedPagePayload):
    herd_id: UUID | None = None
    lot_id: UUID | None = None
    animal_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=40)
    animal_status: Literal["ATIVO", "VENDIDO", "ABATIDO", "MORTO", "RECUPERADO"] | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None


class FarmSummaryPayload(_ScopedPagePayload):
    herd_id: UUID | None = None
    lot_id: UUID | None = None
    lot_category: Literal["ENGORDA", "CRIA", "RECRIA"] | None = None
    active: bool | None = True
    is_exception_lot: bool | None = None
    head_count_min: int | None = Field(default=None, ge=0)
    head_count_max: int | None = Field(default=None, ge=0)
    group_by: Literal["property", "herd", "lot", "lot_category", "exception_lot", "active"] = (
        "property"
    )


class ExceptionSummaryPayload(_ScopedPagePayload):
    herd_id: UUID | None = None
    lot_id: UUID | None = None
    statuses: tuple[Literal["ATIVO", "VENDIDO", "ABATIDO", "MORTO", "RECUPERADO"], ...] | None = (
        Field(default=None, min_length=1, max_length=5)
    )
    category: Literal["BEZERRO", "GARROTE", "BOI", "TOURO"] | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=20)
    reason: str | None = Field(default=None, min_length=1, max_length=120)
    date_from: date | None = None
    date_to: date | None = None
    group_by: Literal["property", "herd", "lot", "status", "category", "reason"] = "status"


class ListMedicinesPayload(_Payload):
    medicine_id: UUID | None = None
    brand_name: str | None = Field(default=None, min_length=1, max_length=80)
    active_ingredient: str | None = Field(default=None, min_length=1, max_length=80)
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="Busca textual por nome comercial ou princípio ativo.",
    )
    therapeutic_category: str | None = Field(default=None, min_length=1, max_length=40)
    needs_prescription: bool | None = None
    active: bool | None = True
    page_size: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


class ListMedicineDocumentsPayload(ListMedicinesPayload):
    updated_from: datetime | None = None
    updated_to: datetime | None = None


class EstimateConsumptionPayload(EstimateMedicineConsumptionRequest):
    as_of: date = Field(
        default_factory=lambda: date.today() - timedelta(days=1),
        description="Último dia encerrado do histórico; quando omitido, usa ontem no servidor.",
    )


MCP_TOOL_PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = {
    "list_properties": ListPropertiesPayload,
    "list_herds": ListHerdsPayload,
    "list_lots": ListLotsPayload,
    "list_exception_animals": ListExceptionAnimalsPayload,
    "list_applications": ListApplicationsPayload,
    "list_application_animals": ListApplicationAnimalsPayload,
    "list_occurrences": ListOccurrencesPayload,
    "list_animal_lot_history": ListAnimalLotHistoryPayload,
    "list_cattle_documents": ListCattleDocumentsPayload,
    "get_farm_summary": FarmSummaryPayload,
    "summarize_exception_animals": ExceptionSummaryPayload,
    "list_medicines": ListMedicinesPayload,
    "list_medicine_documents": ListMedicineDocumentsPayload,
    "estimate_medicine_consumption": EstimateConsumptionPayload,
}
