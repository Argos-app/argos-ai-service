"""Contratos de domínio transportados entre o principal e o serviço A2A."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class _A2AContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class DocumentType(StrEnum):
    LAW = "law"
    TERMS_CONDITIONS = "terms_conditions"


class DocumentSkill(StrEnum):
    DOCUMENTAL = "documental"
    LEGAL = "legal"
    MIXED = "mixed"


class DocumentEvidenceRequirement(StrEnum):
    SOURCE = "source"
    PAGE = "page"
    QUOTED_EXCERPT = "quoted_excerpt"
    JURISDICTION = "jurisdiction"
    DOCUMENT_DATE = "document_date"


class DocumentAnswerStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    ABSTAIN = "abstain"
    CORPUS_UPDATING = "corpus_updating"
    UNAVAILABLE = "unavailable"


class CorpusRefreshStatus(StrEnum):
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    UPDATING = "updating"
    UNAVAILABLE = "unavailable"


class LegalValidityStatus(StrEnum):
    UNKNOWN = "unknown"
    IN_FORCE = "in_force"
    REPEALED = "repealed"
    UNDER_REVIEW = "under_review"


DocumentIdFilter = Annotated[tuple[UUID, ...], Field(min_length=1, max_length=100)]
EvidenceRequirements = Annotated[
    tuple[DocumentEvidenceRequirement, ...], Field(min_length=1, max_length=5)
]
PositivePage = Annotated[StrictInt, Field(ge=1)]
RevisionHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]


def _unique_values(value):
    if value is not None and len(set(value)) != len(value):
        raise ValueError("valores duplicados não são permitidos")
    return value


def _calendar_date(value):
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


class DocumentQuestionRequest(_A2AContract):
    """Pergunta submetida pelo principal; identidade e permissões vêm do transporte."""

    request_id: UUID
    question: str = Field(min_length=1, max_length=4_000)
    skill: DocumentSkill
    document_ids: DocumentIdFilter | None = None
    document_types: (
        Annotated[tuple[DocumentType, ...], Field(min_length=1, max_length=2)] | None
    ) = None
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=120)
    reference_date: date | None = None
    evidence_requirements: EvidenceRequirements = (
        DocumentEvidenceRequirement.SOURCE,
        DocumentEvidenceRequirement.PAGE,
    )
    deadline_at: datetime

    @field_validator("document_ids", "document_types", "evidence_requirements")
    @classmethod
    def validate_unique_values(cls, value):
        return _unique_values(value)

    @field_validator("reference_date", mode="before")
    @classmethod
    def validate_reference_date(cls, value):
        return None if value is None else _calendar_date(value)

    @field_validator("deadline_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at deve incluir timezone")
        return value

    @model_validator(mode="after")
    def validate_skill_scope(self):
        permitted = {
            DocumentSkill.DOCUMENTAL: {DocumentType.TERMS_CONDITIONS},
            DocumentSkill.LEGAL: {DocumentType.LAW},
            DocumentSkill.MIXED: set(DocumentType),
        }[self.skill]
        if self.document_types is not None and not set(self.document_types) <= permitted:
            raise ValueError("tipo documental incompatível com a skill selecionada")
        return self


class DocumentSourceReference(_A2AContract):
    """Evidência rastreável da revisão atual de um documento global."""

    document_id: UUID
    title: str = Field(min_length=1, max_length=200)
    document_type: DocumentType
    origin: str = Field(min_length=1, max_length=500)
    revision_hash: RevisionHash
    page_from: PositivePage
    page_to: PositivePage
    excerpt: str = Field(min_length=1, max_length=2_000)
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=120)
    document_date: date | None = None
    uploaded_at: datetime | None = None
    updated_at: datetime | None = None
    was_updated: bool
    legal_validity: LegalValidityStatus = LegalValidityStatus.UNKNOWN

    @field_validator("document_date", mode="before")
    @classmethod
    def validate_document_date(cls, value):
        return None if value is None else _calendar_date(value)

    @field_validator("uploaded_at", "updated_at")
    @classmethod
    def require_timestamp_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp deve incluir timezone")
        return value

    @model_validator(mode="after")
    def validate_pages(self):
        if self.page_to < self.page_from:
            raise ValueError("page_to não pode ser anterior a page_from")
        if self.page_to - self.page_from >= 3:
            raise ValueError("a referência pode abranger no máximo três páginas")
        return self


class DocumentAnswerResponse(_A2AContract):
    """Resposta A2A com estado explícito, fontes e limitações."""

    request_id: UUID
    status: DocumentAnswerStatus
    answer: str = Field(default="", max_length=8_000)
    sources: Annotated[tuple[DocumentSourceReference, ...], Field(max_length=20)] = ()
    limitations: Annotated[tuple[str, ...], Field(max_length=20)] = ()
    missing_fields: Annotated[tuple[str, ...], Field(max_length=20)] = ()
    corpus_revision: RevisionHash | None = None
    checked_at: datetime | None = None
    refresh_status: CorpusRefreshStatus

    @field_validator("checked_at")
    @classmethod
    def require_checked_at_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("checked_at deve incluir timezone")
        return value

    @model_validator(mode="after")
    def validate_status_evidence(self):
        if self.status == DocumentAnswerStatus.SUCCESS and not self.sources:
            raise ValueError("success exige pelo menos uma fonte")
        if self.status == DocumentAnswerStatus.NEEDS_CLARIFICATION and not self.missing_fields:
            raise ValueError("needs_clarification exige missing_fields")
        if (
            self.status
            in {
                DocumentAnswerStatus.CORPUS_UPDATING,
                DocumentAnswerStatus.UNAVAILABLE,
            }
            and not self.limitations
        ):
            raise ValueError("indisponibilidade exige uma limitação explícita")
        if self.status == DocumentAnswerStatus.CORPUS_UPDATING:
            if self.refresh_status != CorpusRefreshStatus.UPDATING:
                raise ValueError("corpus_updating exige refresh_status=updating")
            if self.sources:
                raise ValueError("corpus_updating não pode expor fontes")
        if self.status == DocumentAnswerStatus.UNAVAILABLE:
            if self.refresh_status != CorpusRefreshStatus.UNAVAILABLE:
                raise ValueError("unavailable exige refresh_status=unavailable")
            if self.sources:
                raise ValueError("unavailable não pode expor fontes")
        return self
