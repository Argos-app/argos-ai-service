"""Contrato tipado para leitura dos documentos de histórico de bovinos."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from argos_mcp_data.domain.cattle import ScopedPageRequest


class ListCattleDocumentsRequest(ScopedPageRequest):
    herd_id: UUID | None = None
    lot_id: UUID | None = None
    animal_id: UUID | None = None
    ear_tag: str | None = Field(default=None, min_length=1, max_length=40)
    animal_status: Literal["ATIVO", "VENDIDO", "ABATIDO", "MORTO", "RECUPERADO"] | None = None
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


class CattleHistoryDocument(BaseModel):
    """Documento Mongo validado; campos adicionais do modelo são preservados."""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str
    relational_property_id: UUID
    herd: dict[str, Any]
    lots: list[dict[str, Any]]
    report_created_at: datetime | None = None
    last_updated_at: datetime | None = None

    @field_validator("id", mode="before")
    @classmethod
    def stringify_document_id(cls, value: Any) -> str:
        return str(value)

    @model_validator(mode="before")
    @classmethod
    def map_mongo_id(cls, value: Any) -> Any:
        if isinstance(value, dict) and "_id" in value and "id" not in value:
            value = {**value, "id": value["_id"]}
            value.pop("_id", None)
        return value

    @model_validator(mode="after")
    def validate_nested_relations(self):
        try:
            UUID(str(self.herd["herd_id"]))
            lot_ids: set[UUID] = set()
            for lot in self.lots:
                lot_id = UUID(str(lot["lot_id"]))
                if lot_id in lot_ids:
                    raise ValueError("lot_id duplicado")
                lot_ids.add(lot_id)
            self._validate_summary_counts()
            if not lot_ids:
                return self
            for lot in self.lots:
                lot_id = UUID(str(lot["lot_id"]))
                if not isinstance(lot.get("applications"), list) or not isinstance(
                    lot.get("animal"), list
                ):
                    raise ValueError("arrays de aplicações/animais ausentes")
                for animal in lot.get("animal", []):
                    UUID(str(animal["animal_exception_id"]))
                    if UUID(str(animal["lot_id"])) != lot_id:
                        raise ValueError("animal associado a lote diferente do documento")
                    if UUID(str(animal["origin_lot_id"])) not in lot_ids:
                        raise ValueError("lote de origem fora do documento")
                    if any(
                        not isinstance(animal.get(key), list)
                        for key in ("exception_applications", "occurrences", "lot_history")
                    ):
                        raise ValueError("arrays do animal ausentes")
                    for item in animal.get("exception_applications", []):
                        if UUID(str(item["lot_id"])) not in lot_ids:
                            raise ValueError("aplicação individual associada a lote externo")
                    for item in animal.get("lot_history", []):
                        if UUID(str(item["lot_id"])) not in lot_ids:
                            raise ValueError("histórico associado a lote externo")
                for application in lot.get("applications", []):
                    UUID(str(application["application_id"]))
                for animal in lot.get("animal", []):
                    for occurrence in animal.get("occurrences", []):
                        UUID(str(occurrence["occurrence_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("documento de histórico bovino inválido ou inconsistente") from exc
        return self

    def _validate_summary_counts(self) -> None:
        summary = (self.model_extra or {}).get("history_summary")
        if summary is None:
            return
        if not isinstance(summary, dict):
            raise ValueError("history_summary inválido")
        animals = [animal for lot in self.lots for animal in lot.get("animal", [])]
        occurrences = [item for animal in animals for item in animal.get("occurrences", [])]
        expected = {
            "total_lots": len(self.lots),
            "total_exception_lots": sum(lot.get("is_exception_lot") is True for lot in self.lots),
            "total_exception_animals": len(
                {str(animal["animal_exception_id"]) for animal in animals}
            ),
            "total_lot_applications": sum(len(lot.get("applications", [])) for lot in self.lots),
            "total_exception_applications": sum(
                len(animal.get("exception_applications", [])) for animal in animals
            ),
            "total_occurrences": len(occurrences),
            "open_occurrence_count": sum(item.get("resolved") is False for item in occurrences),
        }
        if any(key in summary and summary[key] != value for key, value in expected.items()):
            raise ValueError("history_summary diverge dos itens detalhados")


class CattleDocumentsPage(BaseModel):
    items: tuple[CattleHistoryDocument, ...]
    page_size: int
    next_offset: int | None
    has_more: bool
