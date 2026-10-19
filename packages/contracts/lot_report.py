"""Contrato da projeção de um lote produzida pelo MCP de dados.

The projection is intentionally smaller than the relational schema. It contains
only the information needed to describe a lot and its medication history in a
report. The MCP de dados remains responsible for authorization and querying the
source databases.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class LotReportIdentity(_Contract):
    """Identidade e estado do lote, do rebanho e da propriedade."""

    lot_id: UUID
    lot_code: str = Field(min_length=1, max_length=20)
    lot_category: Literal["ENGORDA", "CRIA", "RECRIA"]
    lot_active: bool
    lot_is_exception: bool
    lot_exception_reason: str | None = None
    lot_head_count: int = Field(ge=0)
    lot_open_date: date
    lot_close_date: date | None = None
    slaughter_release_date: date | None = None
    fit_for_slaughter: bool
    herd_id: UUID
    herd_name: str = Field(min_length=1, max_length=60)
    herd_breed: str = Field(min_length=1, max_length=40)
    property_id: UUID
    property_name: str = Field(min_length=1)


class ApplicationRecord(_Contract):
    """Aplicação coletiva registrada para o lote."""

    application_id: UUID
    application_kind: Literal["COLETIVA", "INDIVIDUAL"] = "COLETIVA"
    application_date: date
    application_time: time | None = None
    medicine_name: str = Field(min_length=1, max_length=80)
    active_ingredient: str = Field(min_length=1, max_length=80)
    medicine_batch_number: str = Field(min_length=1, max_length=30)
    dose: Decimal = Field(gt=0)
    unit: Literal["ml", "mg", "g", "UI", "dose"]
    route: Literal[
        "INTRAMUSCULAR",
        "SUBCUTANEA",
        "ORAL",
        "TOPICA",
        "INTRAVENOSA",
    ]
    head_count_applied: int = Field(gt=0)
    meat_withdrawal_end: date | None = None
    active: bool
    goal: str | None = None
    notes: str | None = None


class AnimalExceptionRecord(_Contract):
    """Animal individual acompanhado por estar em exceção."""

    animal_id: UUID
    ear_tag: str = Field(min_length=1, max_length=20)
    category: Literal["BEZERRO", "GARROTE", "BOI", "TOURO"]
    status: Literal["ATIVO", "VENDIDO", "ABATIDO", "MORTO", "RECUPERADO"]
    birth_date: date | None = None
    current_weight: Decimal | None = Field(default=None, gt=0)
    exception_date: date
    exception_end_date: date | None = None
    exception_reason: str = Field(min_length=1)
    slaughter_release_date: date | None = None
    fit_for_slaughter: bool
    notes: str | None = None


class LotReportData(_Contract):
    """Projeção autorizada usada para construir um relatório de lote."""

    schema_version: Literal["lot-report.v1"] = "lot-report.v1"
    requested_lot: LotReportIdentity
    applications: tuple[ApplicationRecord, ...] = ()
    exception_animals: tuple[AnimalExceptionRecord, ...] = ()
    queried_at: datetime
    data_as_of: datetime | None = None


class LotReportEnvelope(_Contract):
    """Envelope assinado de saída do MCP de dados.

    ``valid_until`` delimita a validade da autorização para o uso do payload.
    Ele é parte do conteúdo assinado junto com o relatório, o solicitante e o
    escopo de propriedades.
    """

    report: LotReportData
    principal_user_id: UUID
    authorized_property_ids: tuple[UUID, ...] = Field(min_length=1)
    source_service: Literal["mcp_data"] = "mcp_data"
    valid_until: datetime
    payload_hash: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    signature_key_id: str = Field(min_length=1)
