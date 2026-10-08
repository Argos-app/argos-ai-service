"""Contratos de leitura do catálogo relacional e documentos Mongo de medicamentos."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _MedicineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class _MedicinePageRequest(_MedicineModel):
    medicine_id: UUID | None = None
    brand_name: str | None = Field(default=None, min_length=1, max_length=80)
    active_ingredient: str | None = Field(default=None, min_length=1, max_length=80)
    therapeutic_category: str | None = Field(default=None, min_length=1, max_length=40)
    needs_prescription: bool | None = None
    active: bool | None = True
    page_size: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


class ListMedicinesRequest(_MedicinePageRequest):
    """Filtros para os campos confirmados no cadastro SQL de medicine."""


class ListMedicineDocumentsRequest(_MedicinePageRequest):
    """Filtros para a coleção de detalhes, sem seleção por propriedade."""

    updated_from: datetime | None = None
    updated_to: datetime | None = None

    @field_validator("updated_from", "updated_to")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("filtros de atualização devem incluir fuso horário")
        return value

    @model_validator(mode="after")
    def validate_update_range(self):
        if self.updated_from and self.updated_to and self.updated_from > self.updated_to:
            raise ValueError("updated_from deve ser anterior ou igual a updated_to")
        return self


class MedicineCatalogRecord(_MedicineModel):
    medicine_id: UUID
    supplier_id: UUID
    supplier_name: str
    therapeutic_category_id: UUID
    therapeutic_category: str
    therapeutic_category_description: str | None
    brand_name: str
    active_ingredient: str
    mapa_registration: str | None
    indication: str
    description: str | None
    needs_prescription: bool
    meat_withdrawal_days: int
    dose: Decimal
    unit: Literal["ml", "mg", "g", "UI", "dose"]
    route: Literal["INTRAMUSCULAR", "SUBCUTANEA", "ORAL", "TOPICA", "INTRAVENOSA"] | None
    active: bool


class TherapeuticCategoryDocument(_MedicineModel):
    therapeutic_category_id: UUID
    name: str
    description: str | None = None


class MedicineSupplierDocument(_MedicineModel):
    supplier_id: UUID
    company_name: str
    cnpj: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None


class MedicineDetailDocument(BaseModel):
    """Documento Mongo validado de acordo com a estrutura fornecida."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    relational_medicine_id: UUID
    report_created_at: datetime | None = None
    last_updated_at: datetime | None = None
    brand_name: str
    active_ingredient: str
    mapa_registration: str | None = None
    indication: str
    description: str | None = None
    therapeutic_category: TherapeuticCategoryDocument
    supplier: MedicineSupplierDocument
    needs_prescription: bool
    active: bool
    meat_withdrawal_days: int = Field(ge=0)
    dose: Decimal = Field(gt=0)
    unit: Literal["ml", "mg", "g", "UI", "dose"]
    route: Literal["INTRAMUSCULAR", "SUBCUTANEA", "ORAL", "TOPICA", "INTRAVENOSA"] | None

    @field_validator("id", mode="before")
    @classmethod
    def stringify_document_id(cls, value):
        return str(value)

    @field_validator("relational_medicine_id", mode="before")
    @classmethod
    def decode_medicine_id(cls, value):
        if isinstance(value, (bytes, bytearray)) and len(value) == 16:
            return UUID(bytes=bytes(value))
        return value

    @model_validator(mode="before")
    @classmethod
    def map_mongo_id(cls, value):
        if isinstance(value, dict) and "_id" in value and "id" not in value:
            value = {**value, "id": value["_id"]}
            value.pop("_id", None)
        return value


__all__ = [
    "ListMedicineDocumentsRequest",
    "ListMedicinesRequest",
    "MedicineCatalogRecord",
    "MedicineDetailDocument",
    "MedicineSupplierDocument",
    "TherapeuticCategoryDocument",
]
